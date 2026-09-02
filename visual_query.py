"""Turn a fashion product photo into a concise marketplace search query."""
import base64
import json
import os

import requests


class VisualQueryError(RuntimeError):
    pass


def vision_ready():
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _response_text(payload):
    for output in payload.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    return ""


def analyse_visual_query(data, mime_type):
    """Use a bounded vision request; never invent a brand or exact reference."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise VisualQueryError("L’analyse d’image seule n’est pas encore configurée sur le serveur.")
    image_url = f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"
    prompt = (
        "Analyse ce produit de mode, chaussure ou accessoire pour lancer une recherche shopping. "
        "Retourne uniquement un JSON avec query, brand, category, model, colors, confidence. "
        "query doit être une requête courte en français, exploitable sur des marketplaces. "
        "N'invente jamais une marque, un modèle ou une référence illisible : omets-les si incertains."
    )
    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            timeout=(3, 22),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("LUXE_RADAR_VISION_MODEL", "gpt-5.4-mini"),
                "input": [{"role": "user", "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_url, "detail": "high"},
                ]}],
                "max_output_tokens": 220,
            },
        )
        response.raise_for_status()
        raw = _response_text(response.json()).strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        result = json.loads(raw)
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        raise VisualQueryError("L’analyse visuelle est momentanément indisponible.") from exc
    query = " ".join(str(result.get("query") or "").split())[:120]
    if len(query) < 3:
        raise VisualQueryError("Le produit n’a pas pu être identifié avec assez de précision.")
    return {
        "query": query,
        "brand": str(result.get("brand") or "")[:60],
        "category": str(result.get("category") or "")[:60],
        "model": str(result.get("model") or "")[:80],
        "colors": [str(value)[:30] for value in (result.get("colors") or [])[:4]],
        "confidence": max(0.0, min(1.0, float(result.get("confidence") or 0))),
    }
