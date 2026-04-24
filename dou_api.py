"""
dou_api.py
Busca atos no Diário Oficial da União via portal IN.gov.br.

Fluxo:
  1. GET à página de busca do DOU com os parâmetros informados.
  2. Extrai o array JSON embutido no script tag da resposta HTML.
  3. Filtra por seção (padrão vazio = todas as seções) e retorna DouResult.
  4. A URL permanente de cada ato é construída com urlTitle.
  5. Se `siape` for fornecido, executa busca adicional pelo número e mescla
     os resultados (deduplica por URL).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.in.gov.br"
SEARCH_URL = BASE_URL + "/consulta/-/buscar/dou"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

TIMEOUT = 30
_SECAO_LABEL = {
    "DO1": "Seção 1", "DO2": "Seção 2", "DO3": "Seção 3",
    "DO1A": "Seção 1A", "DO1_EXTRA_A": "Seção 1 Extra A",
    "DO1_EXTRA_B": "Seção 1 Extra B", "DO2A": "Seção 2A",
    "DO3A": "Seção 3A", "DOU_ELETRONICO": "DOU Eletrônico",
}


@dataclass
class DouResult:
    titulo: str
    resumo: str
    data: str
    secao: str
    url: str
    tipo: str = ""
    orgao: str = ""
    fonte: str = "Diário Oficial da União"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _secao_label(raw: str) -> str:
    return _SECAO_LABEL.get(raw.upper(), raw)


def _extrair_tipo(titulo: str) -> str:
    m = re.match(
        r"^(PORTARIA|RESOLU[ÇC][ÃA]O|DESPACHO|DECRETO|INSTRUÇÃO|EDITAL"
        r"|EXTRATO|AVISO|NOTA|ATO|ORDEM DE SERVIÇO|TERMO)",
        titulo.upper(),
    )
    return m.group(1).title() if m else ""


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_url(url_title: str) -> str:
    return f"{BASE_URL}/web/dou/-/{url_title}"


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

def _extract_json_array(html: str) -> list[dict]:
    marker = '"jsonArray":['
    start = html.find(marker)
    if start == -1:
        return []
    bracket_start = start + len(marker) - 1
    depth = 0
    in_string = False
    escape_next = False
    for i in range(bracket_start, len(html)):
        ch = html[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                raw = html[bracket_start: i + 1]
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.warning("jsonArray parse error: %s", exc)
                    return []
    return []


def _parse_json_array(html: str, secao_filter: str) -> list[DouResult]:
    items = _extract_json_array(html)
    if not items:
        logger.debug("jsonArray não encontrado ou vazio na resposta do DOU.")
        return []

    results: list[DouResult] = []
    for item in items:
        pub_name: str = item.get("pubName", "")
        if secao_filter and pub_name.upper() != secao_filter.upper():
            continue
        titulo = item.get("title", "").strip()
        resumo = _clean_html(item.get("content", ""))
        data_pub = item.get("pubDate", "")
        url_title = item.get("urlTitle", "")
        orgao = item.get("hierarchyStr", "")
        tipo = _extrair_tipo(titulo)
        secao = _secao_label(pub_name)
        url = _build_url(url_title) if url_title else BASE_URL
        results.append(DouResult(
            titulo=titulo, resumo=resumo, data=data_pub,
            secao=secao, url=url, tipo=tipo, orgao=orgao,
        ))
    return results


# ---------------------------------------------------------------------------
# Paginação interna
# ---------------------------------------------------------------------------

MAX_DOU_RESULTS = 200
_PAGE_SIZE = 20


def _paginar_dou(base_params: dict, secao: str) -> tuple[list[DouResult], Optional[str]]:
    """Percorre todas as páginas do DOU para um conjunto de parâmetros fixado."""
    all_results: list[DouResult] = []
    urls_vistas: set[str] = set()
    pagina = 1
    while len(all_results) < MAX_DOU_RESULTS:
        # DOU usa offset 1-based: resultado=1 = posição 1, resultado=21 = posição 21, etc.
        params = {**base_params, "resultado": (pagina - 1) * _PAGE_SIZE + 1}
        try:
            resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.Timeout:
            if pagina == 1:
                return [], "Tempo esgotado ao consultar o DOU. Tente novamente."
            break
        except requests.RequestException as exc:
            if pagina == 1:
                return [], f"Erro ao consultar DOU: {exc}"
            break
        html = resp.content.decode("utf-8", errors="replace")
        page_results = _parse_json_array(html, secao)
        logger.debug("DOU offset %d: %d resultado(s) (filtro=%s)", (pagina - 1) * _PAGE_SIZE + 1, len(page_results), secao or "todas")
        novos = [r for r in page_results if r.url not in urls_vistas]
        if not novos:
            break
        all_results.extend(novos)
        urls_vistas.update(r.url for r in novos)
        if len(page_results) < _PAGE_SIZE:
            break
        pagina += 1
    return all_results, None


def _parece_nome_pessoa(termo: str) -> bool:
    """Retorna True se o termo parece um nome de pessoa (≥2 palavras, todas alfabéticas)."""
    palavras = termo.split()
    return len(palavras) >= 2 and all(p.isalpha() for p in palavras)


def _buscar_nome_dou(
    termo_busca: str, date_params: dict, secao: str
) -> tuple[list[DouResult], Optional[str]]:
    """Para nomes multi-palavra, usa frase exata em maiúsculas (como aparecem
    nas portarias). Sem fallback para palavras-chave: se a frase não existir
    no DOU, o resultado correto é 0 — não resultados irrelevantes por OR."""
    palavras = termo_busca.split()
    if len(palavras) >= 2:
        frase = '"' + termo_busca.upper() + '"'
        res, err = _paginar_dou({"q": frase, **date_params}, secao)
        logger.info("DOU nome (frase) '%s': %d resultado(s)", termo_busca, len(res))
        return res, err
    return _paginar_dou({"q": termo_busca, **date_params}, secao)


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def buscar_dou(
    termo: str,
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    secao: str = "",
    siape: str = "",
) -> tuple[list[DouResult], Optional[str]]:
    """
    Busca atos no DOU via portal IN.gov.br.

    Se `siape` for fornecido, executa também uma busca pelo número SIAPE e
    mescla os resultados (deduplica por URL). O número SIAPE aparece no corpo
    das portarias do DOU Seção 2, permitindo localizar atos mesmo quando o
    nome não está indexado com a grafia exata.

    Retorna (lista_de_resultados, mensagem_de_erro_ou_None).
    """
    if not termo.strip() and not siape.strip():
        return [], "Informe um nome ou SIAPE."

    # Remove aspas do input do usuário
    termo_busca = termo.strip().strip('"').strip("'") if termo.strip() else ""

    date_params: dict = {}
    if data_inicio:
        date_params["dataInicio"] = data_inicio.strftime("%d/%m/%Y")
    if data_fim:
        date_params["dataFim"] = data_fim.strftime("%d/%m/%Y")

    all_results: list[DouResult] = []
    urls_vistas: set[str] = set()
    error: Optional[str] = None

    # 1. Busca pelo SIAPE — âncora confiável: número único, encontrado diretamente
    #    pelo motor de busca do DOU independente da grafia do nome no ato.
    if siape.strip():
        res_siape, err_siape = _paginar_dou({"q": siape.strip(), **date_params}, secao)
        if err_siape and not all_results:
            error = err_siape
        novos = [r for r in res_siape if r.url not in urls_vistas]
        if novos:
            logger.info("DOU SIAPE '%s': +%d resultado(s)", siape.strip(), len(novos))
            all_results.extend(novos)
            urls_vistas.update(r.url for r in novos)

    # 2. Busca pelo nome/termo — tenta frase exata em maiúsculas (como aparecem nas
    #    portarias); se retornar 0, cai para palavras-chave como fallback.
    if termo_busca:
        res_nome, err_nome = _buscar_nome_dou(termo_busca, date_params, secao)
        if err_nome and not all_results:
            error = err_nome
        novos_nome = [r for r in res_nome if r.url not in urls_vistas]
        if novos_nome:
            logger.info("DOU nome '%s': +%d resultado(s)", termo_busca, len(novos_nome))
            all_results.extend(novos_nome)
            urls_vistas.update(r.url for r in novos_nome)

    logger.info(
        "DOU: %d resultado(s) total para '%s' siape='%s' (filtro=%s)",
        len(all_results), termo_busca, siape, secao or "todas",
    )
    return all_results, error


# ---------------------------------------------------------------------------
# Execução direta para teste
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    termo_teste = sys.argv[1] if len(sys.argv) > 1 else "progressão"
    print(f"\n=== Testando DOU com termo: '{termo_teste}' (todas as seções) ===\n")

    resultados, erro = buscar_dou(termo_teste, secao="")

    if erro:
        print(f"ERRO: {erro}")
    elif not resultados:
        print("Nenhum resultado encontrado.")
    else:
        print(f"{len(resultados)} resultado(s):\n")
        for r in resultados[:5]:
            print(f"[{r.data}] {r.titulo}")
            print(f"  Órgão: {r.orgao}")
            print(f"  Resumo: {r.resumo[:120]}...")
            print(f"  URL: {r.url}")
            print()
