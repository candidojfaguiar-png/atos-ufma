"""sei_stub.py — Stub para futura integração com o SEI/UFMA."""
from __future__ import annotations
from typing import Any


def buscar_sei(nome: str = "", siape: str = "") -> list[Any]:
    """
    Placeholder para busca no SEI (Sistema Eletrônico de Informações).

    Parâmetros esperados na implementação futura:
        nome  – nome do servidor para localizar processos e documentos
        siape – matrícula SIAPE para filtro adicional

    Retorno esperado: lista de objetos com campos titulo, data, url, tipo, resumo.
    """
    return []
