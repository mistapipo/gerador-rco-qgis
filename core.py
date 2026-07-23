from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class FieldInput:
    name: str
    inferred_typology: str
    type_name: str = ""
    length: int = 0
    precision: int = 0


@dataclass
class FieldResult:
    field_name: str
    inferred_typology: str
    final_typology: str
    description: str
    confidence: str
    source: str
    source_identifier: str | None
    source_page: int | None
    needs_review: bool


@dataclass
class LayerAnalysis:
    layer_name: str
    geometry: str
    fields: list[FieldResult]


def normalize_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


def layer_tokens(value: str | None) -> set[str]:
    ignored = {
        "ide",
        "mg",
        "zap",
        "sub",
        "bacia",
        "do",
        "da",
        "de",
        "dos",
        "das",
        "pto",
        "pol",
        "lin",
        "rco",
        "docx",
        "pdf",
    }
    return {
        token
        for token in normalize_name(value).split("_")
        if token and token not in ignored and not token.isdigit()
    }


def compatible_typology(inferred: str, official: str) -> bool:
    if inferred == official:
        return True
    numeric = {"Numérico", "Numérico (inteiro)", "Numérico (decimal)"}
    return inferred in numeric and official in numeric


def connect_catalog(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _official_candidates(conn: sqlite3.Connection, field_name: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ce.*, s.file_name, s.title, s.identifier, s.geometry
        FROM catalog_entries ce
        JOIN sources s ON s.id = ce.source_id
        WHERE lower(ce.field_name) = lower(?)
        ORDER BY s.imported_at DESC, ce.id DESC
        """,
        (field_name,),
    ).fetchall()
    return [dict(row) for row in rows]


def _fuzzy_candidates(
    conn: sqlite3.Connection, field_name: str, geometry: str
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ce.*, s.file_name, s.title, s.identifier, s.geometry
        FROM catalog_entries ce
        JOIN sources s ON s.id = ce.source_id
        WHERE s.geometry = ? OR s.geometry IS NULL OR s.geometry = ''
        """,
        (geometry,),
    ).fetchall()
    scored: list[dict] = []
    target = normalize_name(field_name)
    for row in rows:
        ratio = difflib.SequenceMatcher(
            None, target, normalize_name(row["field_name"])
        ).ratio()
        if ratio >= 0.78:
            item = dict(row)
            item["similarity"] = ratio
            scored.append(item)
    return sorted(scored, key=lambda item: item["similarity"], reverse=True)[:5]


def match_catalog(
    conn: sqlite3.Connection,
    field_name: str,
    layer_name: str,
    geometry: str,
    inferred_typology: str,
) -> FieldResult:
    layer_norm = normalize_name(layer_name)
    layer_tok = layer_tokens(layer_name)
    candidates = _official_candidates(conn, field_name)
    scored: list[tuple[int, dict]] = []

    for candidate in candidates:
        identifier = candidate.get("identifier") or ""
        title = candidate.get("title") or ""
        filename = candidate.get("file_name") or ""
        source_tokens = (
            layer_tokens(identifier) | layer_tokens(title) | layer_tokens(filename)
        )
        score = 0
        candidate_names = {
            normalize_name(identifier),
            normalize_name(title),
            normalize_name(filename),
        }
        if layer_norm in candidate_names:
            score += 100
        score += len(layer_tok & source_tokens) * 12
        if candidate.get("geometry") == geometry:
            score += 20
        elif candidate.get("geometry"):
            score -= 20
        if compatible_typology(inferred_typology, candidate["typology_normalized"]):
            score += 10
        else:
            score -= 8
        scored.append((score, candidate))

    if scored:
        scored.sort(key=lambda pair: pair[0], reverse=True)
        best_score, best = scored[0]
        same_best = [item for score, item in scored if score == best_score]
        descriptions = {item["description"] for item in same_best}
        exact_layer = layer_norm in {
            normalize_name(best.get("identifier")),
            normalize_name(best.get("title")),
            normalize_name(best.get("file_name")),
        }

        if exact_layer and best_score >= 100:
            confidence = "Confirmado (mesma camada)"
            needs_review = False
        elif best_score >= 54 and len(descriptions) == 1:
            confidence = "Alta confiança"
            needs_review = False
        elif best_score >= 30:
            confidence = "Média - revisar"
            needs_review = True
        else:
            confidence = "Baixa - sugestão"
            needs_review = True

        final_typology = (
            best["typology_normalized"]
            if compatible_typology(inferred_typology, best["typology_normalized"])
            else inferred_typology
        )
        return FieldResult(
            field_name=field_name,
            inferred_typology=inferred_typology,
            final_typology=final_typology,
            description=best["description"],
            confidence=confidence,
            source=best["file_name"],
            source_identifier=best.get("identifier"),
            source_page=best.get("source_page"),
            needs_review=needs_review,
        )

    fuzzy = _fuzzy_candidates(conn, field_name, geometry)
    if fuzzy:
        best = fuzzy[0]
        return FieldResult(
            field_name=field_name,
            inferred_typology=inferred_typology,
            final_typology=inferred_typology,
            description=(
                f"Sugestão baseada no campo semelhante '{best['field_name']}': "
                f"{best['description']}"
            ),
            confidence="Baixa - campo semelhante",
            source=best["file_name"],
            source_identifier=best.get("identifier"),
            source_page=best.get("source_page"),
            needs_review=True,
        )

    return FieldResult(
        field_name=field_name,
        inferred_typology=inferred_typology,
        final_typology=inferred_typology,
        description="Descrição não localizada no catálogo - revisar.",
        confidence="Não encontrado",
        source="",
        source_identifier=None,
        source_page=None,
        needs_review=True,
    )


def analyze_fields(
    conn: sqlite3.Connection,
    layer_name: str,
    geometry: str,
    fields: Iterable[FieldInput],
) -> LayerAnalysis:
    results = [
        match_catalog(
            conn,
            field.name,
            layer_name,
            geometry,
            field.inferred_typology,
        )
        for field in fields
    ]
    return LayerAnalysis(layer_name=layer_name, geometry=geometry, fields=results)
