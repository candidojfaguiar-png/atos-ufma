"""
app.py
Ferramenta de busca de atos administrativos – DIGEP/UFMA.
Fontes: SIPAC/UFMA, DOU (2018+) e DOU Histórico certificado (até 2017).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading

import streamlit as st

# Instala o Chromium do Playwright automaticamente (necessário no Streamlit Cloud)
@st.cache_resource(show_spinner=False)
def _instalar_playwright():
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
    )

_instalar_playwright()
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from dou_api import DouResult, buscar_dou
from dou_historico import DouHistoricoResult, buscar_dou_historico
from sei_stub import buscar_sei
from sipac_scraper import SipacResult, buscar_sipac

logging.basicConfig(level=logging.INFO)

st.set_page_config(
    page_title="Busca de Atos – DIGEP/UFMA",
    page_icon="🔍",
    layout="wide",
)

st.title("Busca de Atos Administrativos")
st.caption("DIGEP/PROGEP – Universidade Federal do Maranhão")

# ---------------------------------------------------------------------------
# Sidebar – parâmetros de busca
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Parâmetros de Busca")

    termo = st.text_input(
        "Nome / termo de busca",
        placeholder="Nome completo, tipo de ato, número…",
        help="Nome do servidor, 'progressão', 'RSC', etc.",
    )
    siape = st.text_input(
        "Matrícula SIAPE",
        placeholder="Ex: 1234567",
        help=(
            "Recomendado: busca pelo SIAPE é mais precisa e independente da "
            "grafia do nome. Usado no SIPAC e no DOU."
        ),
    )
    if not siape.strip():
        st.caption("💡 Prefira informar o SIAPE para resultados mais completos.")

    usar_periodo = st.checkbox("Filtrar por período de publicação")
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    if usar_periodo:
        col1, col2 = st.columns(2)
        with col1:
            data_inicio = st.date_input(
                "De",
                value=date.today() - timedelta(days=365),
                format="DD/MM/YYYY",
            )
        with col2:
            data_fim = st.date_input(
                "Até",
                value=date.today(),
                format="DD/MM/YYYY",
            )

    st.divider()
    st.subheader("Fontes")
    buscar_sipac_cb = st.checkbox("SIPAC/UFMA (Boletim de Serviços)", value=True)
    buscar_dou_cb = st.checkbox("DOU – Diário Oficial da União (2018–hoje)", value=True)
    buscar_hist_cb = st.checkbox(
        "DOU Histórico – versão certificada (até 2017)",
        value=False,
        help=(
            "Busca no portal pesquisa.in.gov.br, que cobre de 1990 a 2017. "
            "Requer filtro de período com data anterior a 2018. "
            "Mais lento — usa navegador headless."
        ),
    )

    pesquisar = st.button("🔍 Pesquisar", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Execução da busca
# ---------------------------------------------------------------------------

if pesquisar:
    if not termo.strip() and not siape.strip():
        st.warning("Informe um nome ou SIAPE.")
        st.stop()

    # Valida DOU Histórico: exige período com data anterior a 2018
    hist_ativo = buscar_hist_cb
    if hist_ativo and usar_periodo and data_inicio and data_inicio.year >= 2018:
        st.warning(
            "O DOU Histórico cobre somente datas anteriores a 2018. "
            "Ajuste o período ou desative essa fonte."
        )
        hist_ativo = False

    _res: dict = {
        "sipac": [], "dou": [], "hist": [],
        "sipac_error": None, "dou_error": None, "hist_error": None,
    }

    def run_sipac():
        if buscar_sipac_cb:
            try:
                r, e = buscar_sipac(
                    termo,
                    data_inicio=data_inicio if usar_periodo else None,
                    data_fim=data_fim if usar_periodo else None,
                    siape=siape,
                )
                _res["sipac"] = r
                _res["sipac_error"] = e
            except Exception as exc:
                logging.exception("run_sipac falhou")
                _res["sipac_error"] = f"Erro inesperado no SIPAC: {exc}"

    def run_dou():
        if buscar_dou_cb:
            try:
                r, e = buscar_dou(
                    termo,
                    data_inicio=data_inicio if usar_periodo else None,
                    data_fim=data_fim if usar_periodo else None,
                    siape=siape,
                )
                _res["dou"] = r
                _res["dou_error"] = e
            except Exception as exc:
                logging.exception("run_dou falhou")
                _res["dou_error"] = f"Erro inesperado no DOU: {exc}"

    def run_hist():
        if hist_ativo:
            try:
                r, e = buscar_dou_historico(
                    termo,
                    data_inicio=data_inicio if usar_periodo else None,
                    data_fim=data_fim if usar_periodo else None,
                )
                _res["hist"] = r
                _res["hist_error"] = e
            except Exception as exc:
                logging.exception("run_hist falhou")
                _res["hist_error"] = f"Erro inesperado no DOU Histórico: {exc}"

    spinner_msg = "Buscando…"
    if buscar_sipac_cb:
        spinner_msg += " (SIPAC pode levar até 2 min)"
    if hist_ativo:
        spinner_msg += " (DOU Histórico também usa navegador — seja paciente)"

    with st.spinner(spinner_msg):
        threads = []
        for fn in [run_sipac, run_dou, run_hist]:
            t = threading.Thread(target=fn, daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

    st.session_state["sipac_results"] = _res["sipac"]
    st.session_state["dou_results"] = _res["dou"]
    st.session_state["hist_results"] = _res["hist"]
    st.session_state["sipac_error"] = _res["sipac_error"]
    st.session_state["dou_error"] = _res["dou_error"]
    st.session_state["hist_error"] = _res["hist_error"]
    st.session_state["termo"] = termo
    st.session_state["siape"] = siape
    st.session_state["hist_ativo"] = hist_ativo
    st.session_state["buscar_sipac_cb"] = buscar_sipac_cb
    st.session_state["buscar_dou_cb"] = buscar_dou_cb

# ---------------------------------------------------------------------------
# Exibição dos resultados
# ---------------------------------------------------------------------------

sipac_results: list[SipacResult] = st.session_state.get("sipac_results", [])
dou_results: list[DouResult] = st.session_state.get("dou_results", [])
hist_results: list[DouHistoricoResult] = st.session_state.get("hist_results", [])
sipac_error = st.session_state.get("sipac_error")
dou_error = st.session_state.get("dou_error")
hist_error = st.session_state.get("hist_error")
termo_exibido = st.session_state.get("termo", "")
siape_exibido = st.session_state.get("siape", "")
hist_ativo = st.session_state.get("hist_ativo", False)
buscar_sipac_cb_used = st.session_state.get("buscar_sipac_cb", True)
buscar_dou_cb_used = st.session_state.get("buscar_dou_cb", True)

nada = (
    not sipac_results and not dou_results and not hist_results
    and not sipac_error and not dou_error and not hist_error
)
if nada:
    st.info("Preencha o termo de busca e clique em **Pesquisar**.")
    st.stop()

_titulo = termo_exibido
if siape_exibido:
    _titulo += f" · SIAPE {siape_exibido}" if _titulo else f"SIAPE {siape_exibido}"
st.markdown(f"### Resultados para: *{_titulo}*")

tab_labels = [
    f"📋 SIPAC/UFMA ({len(sipac_results)} resultado{'s' if len(sipac_results) != 1 else ''})",
    f"📰 DOU ({len(dou_results)} resultado{'s' if len(dou_results) != 1 else ''})",
    f"📜 DOU Histórico ({len(hist_results)} resultado{'s' if len(hist_results) != 1 else ''})",
    "🗂️ SEI",
]
tab_sipac, tab_dou, tab_hist, tab_sei = st.tabs(tab_labels)

# ------------------------------------------------------------------
# Aba SIPAC
# ------------------------------------------------------------------
with tab_sipac:
    if sipac_error:
        st.error(sipac_error)

    if sipac_results:
        if siape_exibido:
            st.caption(f"Busca incluiu matrícula SIAPE: **{siape_exibido}**")

        # Filtro por unidade emissora (client-side)
        unidades_disponiveis = sorted({r.unidade for r in sipac_results if r.unidade})
        filtro_unidades: list[str] = []
        if len(unidades_disponiveis) > 1:
            filtro_unidades = st.multiselect(
                "Filtrar por unidade emissora",
                options=unidades_disponiveis,
                default=[],
                placeholder="Todas as unidades",
            )
        sipac_exibidos = (
            [r for r in sipac_results if r.unidade in filtro_unidades]
            if filtro_unidades else sipac_results
        )

        df_sipac = pd.DataFrame(
            [
                {
                    "Boletim": r.numero_boletim,
                    "Data": r.data,
                    "Tipo": r.tipo,
                    "Unidade": r.unidade,
                    "Assunto": r.assunto,
                    "URL": r.url,
                    "Fonte": r.fonte,
                }
                for r in sipac_results
            ]
        )
        st.download_button(
            "⬇️ Exportar CSV (SIPAC)",
            data=df_sipac.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"sipac_{termo_exibido}.csv",
            mime="text/csv",
        )

        st.markdown(f"**{len(sipac_exibidos)} informativo(s) exibido(s)"
                    + (f" de {len(sipac_results)}" if filtro_unidades else "") + ":**")
        for r in sipac_exibidos:
            col_info, col_link = st.columns([5, 1])
            with col_info:
                st.markdown(
                    f"**{r.assunto}**  \n"
                    f"Boletim {r.numero_boletim} · {r.data} · {r.unidade}"
                )
            with col_link:
                st.link_button("🔗 Ver", r.url, use_container_width=True)
            st.divider()
    elif not sipac_error:
        if buscar_sipac_cb:
            st.info("Nenhum resultado encontrado no SIPAC para este termo.")
        else:
            st.info("Busca no SIPAC desativada.")

# ------------------------------------------------------------------
# Aba DOU (2018+)
# ------------------------------------------------------------------
with tab_dou:
    if dou_error:
        st.error(dou_error)

    if dou_results:
        df_dou = pd.DataFrame(
            [
                {
                    "Data": r.data,
                    "Seção": r.secao,
                    "Tipo": r.tipo,
                    "Título": r.titulo,
                    "Órgão": r.orgao,
                    "Resumo": r.resumo,
                    "URL": r.url,
                    "Fonte": r.fonte,
                }
                for r in dou_results
            ]
        )
        st.download_button(
            "⬇️ Exportar CSV (DOU)",
            data=df_dou.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"dou_{termo_exibido}.csv",
            mime="text/csv",
        )

        # Link para busca completa no portal do DOU (paginação completa)
        _q_dou = siape_exibido if siape_exibido else (
            '"' + termo_exibido.upper() + '"' if termo_exibido else ""
        )
        if _q_dou:
            import urllib.parse
            _url_dou = (
                "https://www.in.gov.br/consulta/-/buscar/dou"
                f"?q={urllib.parse.quote(_q_dou)}&s=todos&exactDate=all&sortType=0"
            )
            st.info(
                f"Exibindo os **{len(dou_results)} resultados mais recentes**. "
                f"O portal do DOU pode ter mais — "
                f"[ver busca completa no DOU ↗]({_url_dou})"
            )

        st.markdown(f"**{len(dou_results)} ato(s) encontrado(s) no Diário Oficial da União:**")
        for r in dou_results:
            with st.expander(f"[{r.data}] {r.titulo}", expanded=False):
                st.markdown(f"**Seção:** {r.secao}  \n**Órgão:** {r.orgao}")
                if r.resumo:
                    st.markdown("**Trecho:**")
                    st.markdown(f"> {r.resumo[:500]}{'…' if len(r.resumo) > 500 else ''}")
                st.link_button("🔗 Abrir no DOU", r.url)
    elif not dou_error:
        if buscar_dou_cb:
            st.info("Nenhum resultado encontrado no DOU para este termo.")
        else:
            st.info("Busca no DOU desativada.")

# ------------------------------------------------------------------
# Aba DOU Histórico (até 2017)
# ------------------------------------------------------------------
with tab_hist:
    if hist_error:
        st.error(hist_error)

    if hist_results:
        df_hist = pd.DataFrame(
            [
                {
                    "Data": r.data,
                    "Seção": r.secao,
                    "Título": r.titulo,
                    "Trecho": r.resumo,
                    "URL": r.url,
                    "Fonte": r.fonte,
                }
                for r in hist_results
            ]
        )
        st.download_button(
            "⬇️ Exportar CSV (DOU Histórico)",
            data=df_hist.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"dou_historico_{termo_exibido}.csv",
            mime="text/csv",
        )

        _termo_hist = termo_exibido or siape_exibido
        st.info(
            f"Exibindo os **{len(hist_results)} resultados mais recentes** (limite por período/página). "
            f"Para busca completa, acesse o portal — "
            f'[pesquisa.in.gov.br ↗](https://pesquisa.in.gov.br/imprensa/core/start.action) '
            f'e pesquise por **{_termo_hist}**.'
        )

        st.markdown(f"**{len(hist_results)} ocorrência(s) encontrada(s) no DOU histórico:**")
        for r in hist_results:
            with st.expander(f"[{r.data}] {r.titulo}", expanded=False):
                st.markdown(f"**Seção:** {r.secao}")
                if r.resumo:
                    st.markdown("**Trecho:**")
                    st.markdown(f"> {r.resumo[:500]}{'…' if len(r.resumo) > 500 else ''}")
                st.link_button("🔗 Abrir página no DOU", r.url)
    elif not hist_error:
        if hist_ativo:
            st.info("Nenhuma ocorrência encontrada no DOU histórico para este termo e período.")
        else:
            st.info(
                "Busca no DOU Histórico desativada.  \n"
                "Ative a opção na barra lateral e use um **período anterior a 2018** para buscar atos publicados entre 1990 e 2017."
            )

# ------------------------------------------------------------------
# Aba SEI
# ------------------------------------------------------------------
with tab_sei:
    import urllib.parse as _up

    _SEI_URL = (
        "https://sei.ufma.br/sei/publicacoes/controlador_publicacoes.php"
        "?acao=publicacao_pesquisar&acao_origem=publicacao_pesquisar&id_orgao_publicacao=0"
    )

    _termo_sei = siape_exibido if siape_exibido else termo_exibido
    if _termo_sei:
        st.markdown(
            "O SEI/UFMA não permite busca automatizada. "
            "Acesse o portal de publicações e pesquise pelo termo abaixo:"
        )
        st.link_button(
            "🔗 Abrir portal de publicações SEI/UFMA",
            _SEI_URL,
            use_container_width=True,
            type="primary",
        )
        st.code(_termo_sei, language=None)
        st.caption("Copie o termo acima e cole no campo de pesquisa do SEI.")
    else:
        st.info("Preencha o nome ou SIAPE e clique em **Pesquisar** para gerar o link de busca no SEI.")

# ------------------------------------------------------------------
# Indicador de cobertura
# ------------------------------------------------------------------
st.divider()
_partes = []
if buscar_sipac_cb_used:
    _cor = "green" if sipac_results else "orange"
    _partes.append(f"**SIPAC/UFMA:** :{_cor}[{len(sipac_results)} resultado{'s' if len(sipac_results) != 1 else ''}]")
if buscar_dou_cb_used:
    _cor = "green" if dou_results else "orange"
    _partes.append(f"**DOU:** :{_cor}[{len(dou_results)} resultado{'s' if len(dou_results) != 1 else ''}]")
if hist_ativo:
    _cor = "green" if hist_results else "orange"
    _partes.append(f"**DOU Histórico:** :{_cor}[{len(hist_results)} resultado{'s' if len(hist_results) != 1 else ''}]")
_partes.append("**SEI:** :gray[integração pendente]")
st.caption("Fontes consultadas: " + " · ".join(_partes))
