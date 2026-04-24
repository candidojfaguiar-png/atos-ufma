"""
dou_historico.py
Busca atos no DOU (versão certificada) para datas anteriores a 2018.
Portal: pesquisa.in.gov.br (Imprensa Nacional)

A busca é feita um ano por vez via Playwright (paginação JavaScript).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://pesquisa.in.gov.br"
SEARCH_URL = BASE_URL + "/imprensa/core/start.action"

_JORNAL_SECAO = {"1": "Seção 1", "2": "Seção 2", "3": "Seção 3"}

MAX_PAGES_PER_YEAR = 10   # 10 × 10 = 100 resultados/ano
MAX_ANOS = 5              # limite de segurança


@dataclass
class DouHistoricoResult:
    titulo: str   # "Seção X · Edição nº Y · dd/MM/yyyy · Pág. Z"
    resumo: str   # trecho do ato com termo destacado
    data: str     # "dd/MM/yyyy"
    secao: str    # "Seção 1" / "Seção 2" / "Seção 3"
    url: str      # link direto para a página do DOU
    fonte: str = "Diário Oficial da União – Versão Certificada"


# ---------------------------------------------------------------------------
# Parsing da página de resultados
# ---------------------------------------------------------------------------

def _parse_results_page(soup: BeautifulSoup) -> tuple[list[DouHistoricoResult], int]:
    """Retorna (resultados, total_paginas)."""
    results: list[DouHistoricoResult] = []

    # Total de páginas a partir do nav de paginação
    total_paginas = 1
    nav = soup.find("ul", {"class": "pagination"})
    if nav:
        m = re.search(r"(\d+)\s+iten?s?\s+encontrados?", nav.get_text(" "), re.I)
        if m:
            total_paginas = max(1, (int(m.group(1)) + 9) // 10)

    table = soup.find("table", {"id": "ResultadoConsulta"})
    tbody = table.find("tbody") if table else None
    if not tbody:
        return results, total_paginas

    for row in tbody.find_all("tr"):
        td = row.find("td")
        if not td:
            continue

        link = td.find("a", {"class": "titulo_jornal"})
        if not link:
            continue

        href = link.get("href", "")
        titulo_text = link.get_text(strip=True)

        # href: ../jsp/visualiza/index.jsp?jornal=3&pagina=36&data=31/03/2017
        m_href = re.search(r"jornal=(\d+)&pagina=(\d+)&data=(\d{2}/\d{2}/\d{4})", href)
        if not m_href:
            continue

        jornal = m_href.group(1)
        data_pub = m_href.group(3)
        secao = _JORNAL_SECAO.get(jornal, f"Jornal {jornal}")
        qs = href.split("index.jsp")[-1]  # ?jornal=...
        url = BASE_URL + "/imprensa/jsp/visualiza/index.jsp" + qs

        # Trecho do ato (texto fora do bloco <nav>)
        snippet_parts = []
        for child in td.children:
            if getattr(child, "name", None) == "nav":
                continue
            text = child.get_text(" ") if hasattr(child, "get_text") else str(child)
            snippet_parts.append(text)
        resumo = re.sub(r"\s+", " ", " ".join(snippet_parts)).strip().lstrip(". ")

        results.append(DouHistoricoResult(
            titulo=titulo_text,
            resumo=resumo[:500],
            data=data_pub,
            secao=secao,
            url=url,
        ))

    return results, total_paginas


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def buscar_dou_historico(
    termo: str,
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
) -> tuple[list[DouHistoricoResult], Optional[str]]:
    """
    Busca no DOU certificado (pesquisa.in.gov.br).

    Cobre datas de 1990 a 2017. Para cada ano no intervalo especificado,
    navega o formulário e pagina os resultados via JavaScript.

    Retorna (lista_de_resultados, mensagem_de_erro_ou_None).
    """
    import asyncio
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    if not termo.strip():
        return [], "Informe um termo de busca."

    termo_busca = termo.strip().strip('"').strip("'")

    # Intervalo de anos limitado a pré-2018
    ano_ini = data_inicio.year if data_inicio else 2015
    ano_fim = min(data_fim.year if data_fim else 2017, 2017)

    if ano_ini > 2017:
        return [], None  # inteiramente coberto pelo DOU moderno

    ano_ini = max(ano_ini, 1990)

    if ano_fim - ano_ini + 1 > MAX_ANOS:
        # Limita a MAX_ANOS anos mais recentes do intervalo
        ano_ini = ano_fim - MAX_ANOS + 1

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return [], "Playwright não instalado."

    all_results: list[DouHistoricoResult] = []
    error: Optional[str] = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"],
        )
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        try:
            for ano in range(ano_ini, ano_fim + 1):
                logger.debug("DOU histórico: buscando ano %d…", ano)

                dt_ini = (
                    data_inicio.strftime("%d/%m")
                    if data_inicio and data_inicio.year == ano
                    else "01/01"
                )
                dt_fim = (
                    data_fim.strftime("%d/%m")
                    if data_fim and data_fim.year == ano
                    else "31/12"
                )

                # Carrega formulário de busca
                page.goto(SEARCH_URL, timeout=30_000)
                page.wait_for_load_state("networkidle", timeout=15_000)

                # Preenche campos
                page.fill('[name="edicao.txtPesquisa"]', f'"{termo_busca}"')
                page.select_option('[name="edicao.ano"]', str(ano))
                page.fill('[name="edicao.dtInicio"]', dt_ini)
                page.fill('[name="edicao.dtFim"]', dt_fim)

                # Marca "Todos" os jornais (primeiro checkbox = Todos)
                todos_cb = page.locator('[name="edicao.jornal"]').first
                if not todos_cb.is_checked():
                    todos_cb.check()

                # Submete busca
                with page.expect_navigation(timeout=30_000, wait_until="commit"):
                    page.click('input[type="submit"][value="consulta"]')
                page.wait_for_load_state("networkidle", timeout=20_000)

                soup = BeautifulSoup(page.content(), "html.parser")
                page_results, total_paginas = _parse_results_page(soup)
                all_results.extend(page_results)

                logger.debug(
                    "DOU histórico %d: pág. 1 → %d resultados, %d página(s) total",
                    ano, len(page_results), total_paginas,
                )

                # Pagina resultados restantes via JavaScript
                for pag in range(2, min(total_paginas, MAX_PAGES_PER_YEAR) + 1):
                    try:
                        with page.expect_navigation(timeout=20_000, wait_until="commit"):
                            page.evaluate(f"submete({pag})")
                        page.wait_for_load_state("networkidle", timeout=20_000)
                        soup = BeautifulSoup(page.content(), "html.parser")
                        pag_results, _ = _parse_results_page(soup)
                        if not pag_results:
                            break
                        all_results.extend(pag_results)
                        logger.debug(
                            "DOU historico %d: pag. %d -> %d resultados",
                            ano, pag, len(pag_results),
                        )
                    except PWTimeout:
                        logger.warning("Timeout pag. %d ano %d; continuando.", pag, ano)
                        break
                    except Exception as exc:
                        logger.warning("Erro pag. %d ano %d: %s", pag, ano, exc)
                        break

                logger.info(
                    "DOU histórico %d: %d resultados acumulados",
                    ano, len(all_results),
                )

        except PWTimeout:
            error = "Tempo esgotado ao consultar DOU histórico."
        except Exception as exc:
            logger.exception("Erro inesperado no dou_historico")
            error = f"Erro ao consultar DOU histórico: {exc}"
        finally:
            ctx.close()
            browser.close()

    return all_results, error


# ---------------------------------------------------------------------------
# Execução direta para teste
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    termo_teste = sys.argv[1] if len(sys.argv) > 1 else "UFMA"
    ano_teste = int(sys.argv[2]) if len(sys.argv) > 2 else 2017
    from datetime import date as dt
    d_ini = dt(ano_teste, 1, 1)
    d_fim = dt(ano_teste, 3, 31)

    print(f"\n=== DOU Historico: '{termo_teste}' ({d_ini} a {d_fim}) ===\n")
    resultados, erro = buscar_dou_historico(termo_teste, d_ini, d_fim)

    if erro:
        print(f"ERRO: {erro}")
    elif not resultados:
        print("Nenhum resultado encontrado.")
    else:
        print(f"{len(resultados)} resultado(s):\n")
        for r in resultados[:5]:
            print(f"[{r.data}] {r.titulo}")
            print(f"  Seção: {r.secao}")
            print(f"  Trecho: {r.resumo[:120]}...")
            print(f"  URL: {r.url}")
            print()
