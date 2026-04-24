"""
sipac_scraper.py
Busca atos no Boletim de Serviços do SIPAC/UFMA.

Fluxo:
  1. Playwright headless: abre a página de busca, preenche os campos, clica
     "Consultar" e aguarda a navegação para a página de resultados.
  2. Extrai a tabela de resultados (class="listagem") com os dados de cada
     informativo e o idSolicitacao embutido no onclick de cada linha.
  3. Usa context.request (Playwright API context, compartilha sessão/cookies)
     para fazer POSTs ao SIPAC por cada resultado. Entre cada detalhe re-envia
     o formulário de busca para obter um ViewState válido.
  4. Retorna lista de SipacResult com URL direta para o PDF (verArquivo).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://sipac.ufma.br"
SEARCH_PATH = "/public/jsp/boletim_servico/busca_avancada.jsf"
SEARCH_URL = BASE_URL + SEARCH_PATH

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


@dataclass
class SipacResult:
    numero_boletim: str      # ex: "5/2026"
    data: str                # data de publicação ex: "11/03/2026"
    assunto: str             # ex: "PORTARIA - 123/2026: Progressão …"
    url: str                 # URL do PDF via verArquivo (permanente)
    id_solicitacao: str = "" # ID interno do informativo
    btn_field: str = ""      # campo-botão do onclick (único por linha)
    tipo: str = ""           # PORTARIA, RESOLUÇÃO etc.
    unidade: str = ""        # Unidade solicitante
    fonte: str = "Boletim de Serviços – SIPAC/UFMA"


# ---------------------------------------------------------------------------
# Helpers de parsing
# ---------------------------------------------------------------------------

def _parse_listagem(soup: BeautifulSoup) -> list[SipacResult]:
    """Extrai resultados da tabela class='listagem'."""
    tbl = soup.find("table", {"class": "listagem"})
    if not tbl:
        return []

    results: list[SipacResult] = []
    rows = tbl.find_all("tr")

    i = 1  # pula cabeçalho (row 0)
    while i < len(rows):
        data_row = rows[i]
        assunto_row = rows[i + 1] if i + 1 < len(rows) else None

        cells = data_row.find_all("td")
        if len(cells) < 6:
            i += 1
            continue

        data_pub = cells[0].get_text(strip=True)
        num_ano = cells[1].get_text(strip=True)
        tipo = cells[2].get_text(strip=True)
        unidade = cells[3].get_text(strip=True)
        data_publicacao = cells[4].get_text(strip=True)
        boletim = cells[5].get_text(strip=True)

        # Extrai idSolicitacao e btn_field do onclick do link "Detalhar"
        id_solic = ""
        btn_field = ""
        if len(cells) > 6:
            link = cells[6].find("a", attrs={"onclick": True})
            if link:
                onclick = link["onclick"]
                m = re.search(r"'idSolicitacao'\s*:\s*'(\d+)'", onclick)
                if m:
                    id_solic = m.group(1)
                # O primeiro par chave:valor (onde chave==valor) é o campo-botão
                m2 = re.search(r"'([^']+)'\s*:\s*'(\1)'", onclick)
                if m2:
                    btn_field = m2.group(1)

        # Assunto da linha seguinte
        assunto_raw = assunto_row.get_text(" ", strip=True) if assunto_row else ""
        assunto = re.sub(r"^Assunto\s*:\s*", "", assunto_raw, flags=re.I)

        descricao = f"{tipo} - {num_ano}: {assunto}" if assunto else f"{tipo} - {num_ano}"

        # URL direta: VerInformativo é público e não exige login
        doc_url = (
            f"{BASE_URL}/sipac/VerInformativo?id={id_solic}"
            if id_solic
            else SEARCH_URL
        )

        results.append(
            SipacResult(
                numero_boletim=boletim,
                data=data_publicacao or data_pub,
                assunto=descricao,
                url=doc_url,
                id_solicitacao=id_solic,
                btn_field=btn_field,
                tipo=tipo,
                unidade=unidade,
            )
        )

        i += 2  # cada informativo ocupa 2 linhas (dados + assunto)

    return results


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _submeter_busca(page, termo: str, data_inicio, data_fim, PWTimeout) -> list[SipacResult]:
    """Navega para o formulário SIPAC, submete a busca e retorna resultados."""
    page.goto(SEARCH_URL, timeout=45_000)
    page.wait_for_load_state("networkidle", timeout=20_000)

    page.check('[name="form:checkConteudo"]')
    page.fill('[name="form:conteudo"]', termo)

    if data_inicio or data_fim:
        page.check('[name="form:checkPeriodoPublicacao"]')
        if data_inicio:
            page.fill(
                '[name="form:j_id_jsp_111637002_24"]',
                data_inicio.strftime("%d/%m/%Y"),
            )
        if data_fim:
            page.fill(
                '[name="form:j_id_jsp_111637002_25"]',
                data_fim.strftime("%d/%m/%Y"),
            )

    with page.expect_navigation(timeout=90_000, wait_until="commit"):
        page.click('input[value="Consultar"]', timeout=90_000)
    page.wait_for_load_state("networkidle", timeout=30_000)

    soup = BeautifulSoup(page.content(), "html.parser")
    results = _parse_listagem(soup)
    logger.debug("SIPAC '%s': %d resultado(s)", termo, len(results))
    return results


# ---------------------------------------------------------------------------
# Função principal via Playwright
# ---------------------------------------------------------------------------

def buscar_sipac(
    termo: str,
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    siape: str = "",
) -> tuple[list[SipacResult], Optional[str]]:
    """
    Busca atos no Boletim de Serviços do SIPAC/UFMA.

    Para termos com múltiplas palavras (ex: nome completo), busca o termo
    completo e também cada palavra individualmente, mesclando os resultados
    por id_solicitacao para maximizar a cobertura.

    Retorna (lista_de_resultados, mensagem_de_erro_ou_None).
    """
    import asyncio
    import sys
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    if not termo.strip() and not siape.strip():
        return [], "Informe um nome ou SIAPE."

    # SIPAC não suporta aspas — remove delimitadores; conteúdo é armazenado em maiúsculas
    termo = termo.strip().strip('"').strip("'").upper() if termo.strip() else ""

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return [], (
            "Playwright não instalado. Execute:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )

    results: list[SipacResult] = []
    error: Optional[str] = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--no-first-run",
                "--mute-audio",
                "--hide-scrollbars",
            ],
        )
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        try:
            # 1. Busca pelo termo exato
            if termo:
                logger.debug("SIPAC: buscando '%s'…", termo)
                results = _submeter_busca(page, termo, data_inicio, data_fim, PWTimeout)
                logger.info("SIPAC: %d resultado(s) para '%s'", len(results), termo)

            ids_vistos = {r.id_solicitacao for r in results if r.id_solicitacao}

            # 2. Busca pelo SIAPE (complementa resultados sem duplicar)
            if siape.strip():
                try:
                    siape_res = _submeter_busca(
                        page, siape.strip(), data_inicio, data_fim, PWTimeout
                    )
                    novos_siape = [
                        r for r in siape_res
                        if r.id_solicitacao and r.id_solicitacao not in ids_vistos
                    ]
                    if novos_siape:
                        logger.info(
                            "SIPAC SIAPE '%s': +%d resultado(s)", siape, len(novos_siape)
                        )
                        results.extend(novos_siape)
                        ids_vistos.update(r.id_solicitacao for r in novos_siape)
                except PWTimeout:
                    logger.warning("Timeout buscando SIAPE '%s'.", siape)
                except Exception as exc:
                    logger.warning("Erro buscando SIAPE '%s': %s", siape, exc)

            if not results:
                logger.debug("SIPAC: nenhum resultado.")

        except PWTimeout:
            logger.warning("Timeout ao buscar SIPAC com termo '%s'.", termo)
            error = (
                f"Tempo esgotado ao buscar '{termo}' no SIPAC. "
                "Tente novamente ou refine o termo."
            )
        except Exception as exc:
            logger.exception("Erro inesperado no sipac_scraper")
            error = f"Erro inesperado ao consultar SIPAC: {exc}"
        finally:
            context.close()
            browser.close()

    return results, error


# ---------------------------------------------------------------------------
# Execução direta para teste
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    termo_teste = sys.argv[1] if len(sys.argv) > 1 else "progressão"
    print(f"\n=== Testando SIPAC/UFMA com termo: '{termo_teste}' ===\n")

    resultados, erro = buscar_sipac(termo_teste)

    if erro:
        print(f"ERRO: {erro}")
    elif not resultados:
        print("Nenhum resultado encontrado para este termo.")
    else:
        print(f"{len(resultados)} resultado(s) encontrado(s):\n")
        for r in resultados[:5]:
            print(json.dumps(r.__dict__, ensure_ascii=False, indent=2))
            print()
