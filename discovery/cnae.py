"""Tabela CNAE 2.0 do IBGE (KL-55) — download/cache + lookup hierárquico.

CNAE = Classificação Nacional de Atividades Econômicas. Usada como referência
estrutural para a classificação multi-setor dos alvos: a IA (e a Receita Federal)
retornam códigos CNAE; aqui derivamos a **divisão** (2 dígitos) e a **seção** (A–U)
de cada código e, opcionalmente, validamos/descrevemos contra a tabela oficial.

Design:
- `derive_division`/`derive_section` são **puros e offline** (mapa CNAE 2.0 embutido) —
  funcionam sem rede, então a classificação nunca depende do download.
- `CNAETable` baixa a tabela do IBGE em **runtime** (nunca em build/CI), cacheia em
  disco (TTL 30d) e é **fail-open**: sem rede e sem cache, fica vazia e só a validação
  de descrição/existência é pulada (os códigos ainda são gravados).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

import httpx

IBGE_CNAE_URL = "https://servicodados.ibge.gov.br/api/v2/cnae"
CACHE_DIR = os.environ.get("KLARIM_CACHE_DIR", "/tmp/klarim")
CACHE_FILE = os.path.join(CACHE_DIR, "cnae_table.json")
CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 dias — CNAE muda raramente

# --------------------------------------------------------------------------- #
# Estrutura CNAE 2.0: seção (A–U) → faixa de divisões (2 dígitos). Embutido para
# derivar seção/divisão OFFLINE (a tabela do IBGE só acrescenta descrições).
# --------------------------------------------------------------------------- #
SECTION_LABELS: Dict[str, str] = {
    "A": "Agricultura, pecuária, produção florestal, pesca e aquicultura",
    "B": "Indústrias extrativas",
    "C": "Indústrias de transformação",
    "D": "Eletricidade e gás",
    "E": "Água, esgoto, atividades de gestão de resíduos e descontaminação",
    "F": "Construção",
    "G": "Comércio; reparação de veículos automotores e motocicletas",
    "H": "Transporte, armazenagem e correio",
    "I": "Alojamento e alimentação",
    "J": "Informação e comunicação",
    "K": "Atividades financeiras, de seguros e serviços relacionados",
    "L": "Atividades imobiliárias",
    "M": "Atividades profissionais, científicas e técnicas",
    "N": "Atividades administrativas e serviços complementares",
    "O": "Administração pública, defesa e seguridade social",
    "P": "Educação",
    "Q": "Saúde humana e serviços sociais",
    "R": "Artes, cultura, esporte e recreação",
    "S": "Outras atividades de serviços",
    "T": "Serviços domésticos",
    "U": "Organismos internacionais e outras instituições extraterritoriais",
}

# seção → (div_min, div_max) inclusivo
_SECTION_RANGES = [
    ("A", 1, 3), ("B", 5, 9), ("C", 10, 33), ("D", 35, 35), ("E", 36, 39),
    ("F", 41, 43), ("G", 45, 47), ("H", 49, 53), ("I", 55, 56), ("J", 58, 63),
    ("K", 64, 66), ("L", 68, 68), ("M", 69, 75), ("N", 77, 82), ("O", 84, 84),
    ("P", 85, 85), ("Q", 86, 88), ("R", 90, 93), ("S", 94, 96), ("T", 97, 97),
    ("U", 99, 99),
]


def _digits(code: str) -> str:
    """Só os dígitos de um código CNAE ('62.01-5/00' → '6201500')."""
    return re.sub(r"\D", "", code or "")


def derive_division(code: str) -> Optional[str]:
    """Divisão (2 primeiros dígitos) de um código CNAE. ``None`` se inválido."""
    d = _digits(code)
    if len(d) < 2:
        return None
    return d[:2]


def derive_section(code: str) -> Optional[str]:
    """Seção (A–U) de um código CNAE, pela divisão. ``None`` se fora da faixa."""
    div = derive_division(code)
    if div is None:
        return None
    n = int(div)
    for sec, lo, hi in _SECTION_RANGES:
        if lo <= n <= hi:
            return sec
    return None


def format_cnae(raw) -> str:
    """Normaliza um código CNAE para o formato classe ``NN.NN-N`` quando tem 5
    dígitos (ex.: 6201500 → '62.01-5'); senão devolve os dígitos limpos."""
    d = _digits(str(raw))
    if len(d) >= 5:
        return f"{d[0:2]}.{d[2:4]}-{d[4]}"
    if len(d) == 4:  # grupo NNN.N? na verdade grupo tem 3 dígitos: NN.N
        return f"{d[0:2]}.{d[2:4]}"
    if len(d) == 3:
        return f"{d[0:2]}.{d[2]}"
    return d


class CNAETable:
    """Tabela CNAE com lookup por código. Fail-open (vazia se sem rede/cache)."""

    def __init__(self) -> None:
        self._classes: Dict[str, dict] = {}  # dígitos da classe (5) → {descricao,...}
        self._loaded = False

    # --- carga ---------------------------------------------------------- #
    def _load_from_cache(self) -> bool:
        try:
            if not os.path.exists(CACHE_FILE):
                return False
            if time.time() - os.path.getmtime(CACHE_FILE) > CACHE_TTL_SECONDS:
                return False
            with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                self._classes = json.load(fh)
            return bool(self._classes)
        except Exception:  # noqa: BLE001 - cache corrompido → re-download
            return False

    def _save_cache(self) -> None:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._classes, fh, ensure_ascii=False)
            os.replace(tmp, CACHE_FILE)  # escrita atômica
        except Exception:  # noqa: BLE001 - sem cache é aceitável
            pass

    async def _download(self) -> None:
        """Baixa /classes do IBGE (cada classe já traz a hierarquia). Fail-open."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{IBGE_CNAE_URL}/classes")
                resp.raise_for_status()
                data = resp.json()
            classes: Dict[str, dict] = {}
            for item in data:
                cid = _digits(str(item.get("id", "")))
                if len(cid) < 5:
                    continue
                classes[cid[:5]] = {
                    "descricao": (item.get("descricao") or "").strip(),
                    "division": cid[:2],
                    "section": derive_section(cid),
                }
            if classes:
                self._classes = classes
                self._save_cache()
        except Exception as exc:  # noqa: BLE001 - IBGE fora do ar → tabela vazia
            print(f"[cnae] download IBGE falhou: {exc!r}", flush=True)

    async def ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._load_from_cache():
            await self._download()
        self._loaded = True

    # --- lookup --------------------------------------------------------- #
    def lookup(self, code: str) -> Optional[dict]:
        """Info de um código CNAE (classe). ``None`` se não está na tabela."""
        d = _digits(code)
        if len(d) < 5:
            return None
        return self._classes.get(d[:5])

    def validate_code(self, code: str) -> bool:
        """Se a tabela está carregada, verifica se a classe existe; se a tabela
        está vazia (fail-open), aceita qualquer código com 5+ dígitos."""
        if not self._classes:
            return len(_digits(code)) >= 5
        return self.lookup(code) is not None

    def describe(self, code: str) -> Optional[str]:
        info = self.lookup(code)
        return info.get("descricao") if info else None


# Singleton -------------------------------------------------------------- #
_cnae_table: Optional[CNAETable] = None


async def get_cnae_table() -> CNAETable:
    global _cnae_table
    if _cnae_table is None:
        _cnae_table = CNAETable()
    await _cnae_table.ensure_loaded()
    return _cnae_table


def sections() -> List[dict]:
    """As 21 seções CNAE (id + descrição) — para o endpoint /cnaes/sections."""
    return [{"id": sid, "descricao": label} for sid, label in SECTION_LABELS.items()]


def divisions() -> List[dict]:
    """As 87 divisões (id 2 dígitos + seção) — derivadas do mapa embutido."""
    out: List[dict] = []
    for sec, lo, hi in _SECTION_RANGES:
        for n in range(lo, hi + 1):
            out.append({"id": f"{n:02d}", "section": sec})
    return out
