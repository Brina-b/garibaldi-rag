"""baseline_naive_rag.py — starter e2e per l'hackathon «Il caso dei Mille».

Questo file è la baseline ufficiale da lanciare: ingest (Docling → chunk → embed →
Qdrant) + naive dense RAG + scrittura/validazione di submission.json.

In produzione conviene spezzare organizzare le varie parti
in moduli separati. Qui restano insieme per chiarezza didattica.

Requisiti: OPENAI_API_KEY nel .env (embedding + LLM). Nessun fallback senza API.

Esempio:
  python baseline_naive_rag.py \\
    --questions eval/sample_questions.json \\
    --round-id sample \\
    --output outputs/submission_sample.json \\
    --rebuild
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import re
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ---------------------------------------------------------------------------
# Contratto submission (= forma di eval/submission.example.json)
# ConfigDict(extra="forbid"): campi non previsti → errore (come additionalProperties: false).
# ValidationError: eccezione di Pydantic quando il JSON non rispetta i Field.
# ---------------------------------------------------------------------------


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, le=5)
    document_id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ModelCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: int = Field(ge=0)
    declared_cost_eur: float = Field(ge=0)
    model_calls: list[ModelCall] = Field(default_factory=list)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    contexts: list[Context] = Field(default_factory=list, max_length=5)
    telemetry: Telemetry


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    round_id: str = Field(min_length=1)
    answers: list[Answer] = Field(min_length=1)


def validate_payload(
    payload: dict,
    questions: dict | None = None,
    manifest: dict | None = None,
    round_id: str | None = None,
) -> list[str]:
    errors: list[str] = []
    try:
        submission = Submission.model_validate(payload)
    except ValidationError as exc:
        return [error["msg"] + " @ " + ".".join(map(str, error["loc"])) for error in exc.errors()]
    for answer in submission.answers:
        ranks = [context.rank for context in answer.contexts]
        if ranks != list(range(1, len(ranks) + 1)):
            errors.append(f"{answer.question_id}: i rank dei contesti devono essere 1..N senza buchi")
    ids = [answer.question_id for answer in submission.answers]
    if len(ids) != len(set(ids)):
        errors.append("question_id duplicati")
    if round_id and submission.round_id != round_id:
        errors.append(f"round_id atteso {round_id}, ricevuto {submission.round_id}")
    if questions:
        expected = [row["question_id"] for row in questions["questions"]]
        actual = [row.question_id for row in submission.answers]
        if set(actual) != set(expected) or len(actual) != len(expected):
            errors.append(f"Copertura question_id non esatta. Attesi {expected}; ricevuti {actual}")
    if manifest:
        allowed = {row["document_id"] for row in manifest["documents"]}
        for answer in submission.answers:
            for context in answer.contexts:
                if context.document_id not in allowed:
                    errors.append(f"{answer.question_id}: document_id non disponibile: {context.document_id}")
    return errors


# ---------------------------------------------------------------------------
# Telemetria / costo autodichiarato
# ---------------------------------------------------------------------------


def declared_cost_eur(
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    override = os.getenv("DECLARED_COST_EUR")
    if override is not None and override.strip() != "":
        return max(0.0, float(override))
    input_rate = float(os.getenv("COST_INPUT_PER_MILLION_EUR", "0") or 0)
    output_rate = float(os.getenv("COST_OUTPUT_PER_MILLION_EUR", "0") or 0)
    cached_rate = float(os.getenv("COST_CACHED_INPUT_PER_MILLION_EUR", str(input_rate)) or 0)
    return round(
        (input_tokens / 1_000_000) * input_rate
        + (output_tokens / 1_000_000) * output_rate
        + (cached_input_tokens / 1_000_000) * cached_rate,
        6,
    )


def _token_usage(response: Any) -> tuple[int, int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    input_tokens = int(getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", 0) or 0)
    cached = int(getattr(usage, "cached_input_tokens", 0) or 0)
    return input_tokens, output_tokens, cached


def _meta_get(metadata: Any, key: str, default: str = "") -> str:
    if metadata is None:
        return default
    if isinstance(metadata, dict):
        value = metadata.get(key, default)
    else:
        value = getattr(metadata, key, default)
    return default if value is None else str(value)


# ---------------------------------------------------------------------------
# Scelta file primario dal manifest
# ---------------------------------------------------------------------------


def choose_primary(document: dict, project_root: Path) -> Path | None:
    """Un documento → un file. Se per errore ce ne fossero più, vince il PDF."""
    candidates: list[Path] = []
    for record in document.get("file_records", []):
        path = project_root / record["path"]
        if path.is_file():
            candidates.append(path)
    if not candidates:
        return None
    if len(candidates) > 1:
        candidates.sort(key=lambda p: (0 if p.suffix.lower() == ".pdf" else 1, p.name))
        print(f"warning: {document['document_id']} ha {len(candidates)} file; uso {candidates[0].name}")
    return candidates[0]


def iter_ingest_files(manifest: dict, project_root: Path) -> list[tuple[Path, str, str]]:
    rows: list[tuple[Path, str, str]] = []
    for document in manifest["documents"]:
        primary = choose_primary(document, project_root)
        if primary is None:
            print(f"skip (nessun file): {document['document_id']}")
            continue
        reliability = document.get("reliability", "unknown")
        rows.append((primary, document["document_id"], reliability))
    return rows


# ---------------------------------------------------------------------------
# Lab_08: ingest + naive dense RAG
# ---------------------------------------------------------------------------


EMB_NAME = "content_embedding"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


def _local_qdrant(path: Path):
    """Qdrant embedded su disco.

    datapizza-ai espone `location`/`host`; per path locale inizializziamo il wrapper
    come fa il client sottostante (`QdrantClient(path=...)`).
    """
    from datapizza.vectorstores.qdrant import QdrantVectorstore

    store = QdrantVectorstore.__new__(QdrantVectorstore)
    store.host = None
    store.port = 6333
    store.api_key = None
    store.kwargs = {"path": str(path)}
    store.batch_size = 100
    return store


def build_clients(api_key: str, model: str):
    from datapizza.clients.openai import OpenAIClient
    from datapizza.embedders.openai import OpenAIEmbedder

    embedder = OpenAIEmbedder(api_key=api_key, model_name=EMBEDDING_MODEL)
    llm = OpenAIClient(api_key=api_key, model=model)
    return embedder, llm


def ingest_corpus(
    *,
    files: list[tuple[Path, str]],
    embedder,
    qdrant_path: Path,
    collection: str,
    rebuild: bool,
):
    from datapizza.core.vectorstore import VectorConfig
    from datapizza.embedders import ChunkEmbedder
    from datapizza.modules.parsers.docling import DoclingParser
    from datapizza.modules.splitters import RecursiveSplitter
    from datapizza.pipeline import IngestionPipeline

    if rebuild and qdrant_path.exists():
        shutil.rmtree(qdrant_path)
    qdrant_path.mkdir(parents=True, exist_ok=True)

    vector_store = _local_qdrant(qdrant_path)
    client = vector_store.get_client()
    if client.collection_exists(collection):
        if rebuild:
            vector_store.delete_collection(collection)
        else:
            n_chunk = len(list(vector_store.dump_collection(collection)))
            print(f"Indice esistente: {n_chunk} chunk in '{collection}' ({qdrant_path})")
            return vector_store

    vector_store.create_collection(
        collection_name=collection,
        vector_config=[VectorConfig(dimensions=EMBEDDING_DIM, name=EMB_NAME)],
    )
    ingestion = IngestionPipeline(
        modules=[
            DoclingParser(),
            RecursiveSplitter(max_char=1024, overlap=128),
            ChunkEmbedder(client=embedder, embedding_name=EMB_NAME),
        ],
        vector_store=vector_store,
        collection_name=collection,
    )
    for path, document_id, reliability in files:
        ingestion.run(
            file_path=str(path),
            metadata={"document_id": document_id, "source_file": path.name, "reliability": reliability},
        )
        print(f"ingest: {document_id} <- {path.name} [reliability={reliability}]")
    n_chunk = len(list(vector_store.dump_collection(collection)))
    print(f"Ingest completato: {n_chunk} chunk in '{collection}'")
    return vector_store


GROUNDING = (
    "Rispondi SOLO usando il contesto fornito. Ogni brano inizia con il suo document_id tra "
    "parentesi quadre, es. [cronologia_ufficiale_01]. Cita SEMPRE la fonte scrivendo ESATTAMENTE "
    "[document_id_reale] incollato nel testo, es. [cronologia_ufficiale_01] — non scrivere mai "
    "la parola 'document_id' come testo, non aggiungere 'Fonte:' o altre parole subito prima "
    "della parentesi quadra. Copia il document_id ESATTAMENTE come appare tra le parentesi quadre "
    "del brano di origine, senza abbreviarlo, tagliarlo o modificarlo in alcun modo. "
    "Puoi citare ESCLUSIVAMENTE i document_id che compaiono per intero tra parentesi quadre nei "
    "brani mostrati qui sopra in QUESTO messaggio. Se un brano nomina un'altra fonte solo per "
    "titolo o descrizione (es. 'nota di rettifica del comitato') senza che quella fonte sia "
    "mostrata qui con il proprio [document_id], NON dedurre o inventare il suo document_id: cita "
    "invece il document_id del brano che stai effettivamente leggendo, oppure ometti la citazione "
    "se non è indispensabile. "
    "Ogni brano è anche preceduto da un'indicazione tra parentesi tonde con l'affidabilità dichiarata "
    "della fonte. Se non è neutra (es. 'propaganda', 'obsolete', 'harmful_if_unqualified', 'contested', "
    "'allusive', 'incomplete'), qualificalo esplicitamente nella risposta invece di presentarlo come "
    "fatto neutro (es. 'una fonte propagandistica sostiene...', 'una versione poi corretta affermava...'). "
    "Se una nota è 'corrective' rispetto a una 'obsolete' sullo stesso fatto, preferisci la versione "
    "corretta nella risposta principale, ma cita comunque quella precedente se la domanda la richiede. "
    "Se il contesto non contiene la risposta, di' esattamente: 'Non lo so'."
)
def build_naive_dag(embedder, vector_store, llm):
    from datapizza.modules.prompt import ChatPromptTemplate
    from datapizza.pipeline import DagPipeline

    prompt_template = ChatPromptTemplate(
        user_prompt_template="Domanda: {{ user_prompt }}",
        retrieval_prompt_template=(
            "{% for chunk in chunks %}[{{ chunk.metadata.document_id }}] (affidabilità dichiarata: {{ chunk.metadata.reliability }})\n{{ chunk.text }}\n\n{% endfor %}"
        ),
    )
    dag = DagPipeline()
    dag.add_module("embedder", embedder)
    dag.add_module("retriever", vector_store.as_retriever())
    dag.add_module("prompt_template", prompt_template)
    dag.add_module("llm", llm)
    dag.connect("embedder", "retriever", target_key="query_vector")
    dag.connect("retriever", "prompt_template", target_key="chunks")
    dag.connect("prompt_template", "llm", target_key="memory")
    return dag


def dense_retrieve(embedder, vector_store, collection: str, domanda: str, k: int = 5):
    query_vector = embedder.embed(domanda)
    return vector_store.search(
        collection_name=collection,
        query_vector=query_vector,
        k=k,
        vector_name=EMB_NAME,
    )


def naive_rag(dag, collection: str, domanda: str, k: int = 5, k_retrieve: int | None = None):
    """RAG densa completa (retrieve + generate). La baseline Lab_08.

    k_retrieve: quanti candidati chiedere a Qdrant (>= k). Il dedup in hits_to_contexts
    taglia poi a k dopo aver tolto i quasi-duplicati. Se None, usa k (comportamento originale).
    """
    retrieve_k = k_retrieve or k
    return dag.run(
        {
            "embedder": {"text": domanda},
            "retriever": {"collection_name": collection, "k": retrieve_k, "vector_name": EMB_NAME},
            "prompt_template": {"user_prompt": domanda},
            "llm": {"input": domanda, "system_prompt": GROUNDING},
        }
    )


def hits_to_contexts(hits: list[Any], max_contexts: int = 5, max_per_doc: int = 1) -> list[dict]:
    contexts: list[dict] = []
    seen_by_doc: dict[str, list[str]] = {}
    doc_count: dict[str, int] = {}
    leftover: list[tuple[str, str]] = []  # scartati solo per il cap, non duplicati veri

    for hit in hits:
        if len(contexts) >= max_contexts:
            break
        document_id = _meta_get(getattr(hit, "metadata", None), "document_id")
        content = (getattr(hit, "text", None) or "").strip()
        if not document_id or not content:
            continue
        norm = " ".join(content.split()).lower()
        prior_norms = seen_by_doc.get(document_id, [])
        is_dup = any(norm == p or norm in p or p in norm for p in prior_norms)
        if is_dup:
            continue
        seen_by_doc.setdefault(document_id, []).append(norm)
        if doc_count.get(document_id, 0) >= max_per_doc:
            leftover.append((document_id, content))
            continue
        doc_count[document_id] = doc_count.get(document_id, 0) + 1
        contexts.append({"rank": 0, "document_id": document_id, "content": content})

    for document_id, content in leftover:
        if len(contexts) >= max_contexts:
            break
        contexts.append({"rank": 0, "document_id": document_id, "content": content})

    for i, ctx in enumerate(contexts, start=1):
        ctx["rank"] = i
    return contexts

_STOPWORDS = {
    "il", "lo", "la", "i", "gli", "le", "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "un", "uno", "una", "che", "non", "e", "ma", "si", "al", "del", "della", "dei", "delle",
    "come", "anche", "se", "questo", "questa", "questi", "queste", "sono", "essere", "stato",
}


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zàèéìòùç]+", text.lower()) if len(w) > 3 and w not in _STOPWORDS}


def fix_citations(answer_text: str, contexts: list[dict]) -> str:
    """Corregge le citazioni nella risposta:
    1) ID troncati -> ID reale per suffisso (es. comitato_palermo_v2 -> nota_comitato_palermo_v2)
    2) ID non presenti nei contexts di questa domanda -> riassegnati al context il cui
       contenuto condivide piu' parole chiave con la frase citata, se il riscontro e' netto;
       altrimenti la citazione viene rimossa (meglio nessuna citazione che una falsa)."""
    real_ids = [c["document_id"] for c in contexts]
    real_id_set = set(real_ids)

    def _replace(match: re.Match) -> str:
        candidate = match.group(1)
        if candidate in real_id_set:
            return match.group(0)

        suffix_matches = [rid for rid in real_ids if rid.endswith("_" + candidate) or rid.endswith(candidate)]
        if len(set(suffix_matches)) == 1:
            return f"[{suffix_matches[0]}]"

        window_start = max(0, match.start() - 300)
        claim_words = _keywords(answer_text[window_start:match.start()])
        best_id, best_score = None, 0
        for c in contexts:
            overlap = len(claim_words & _keywords(c["content"]))
            if overlap > best_score:
                best_score, best_id = overlap, c["document_id"]
        if best_id and best_score >= 3:
            return f"[{best_id}]"
        return ""

    fixed = re.sub(r"\[([a-zA-Z0-9_\-]+)\]", _replace, answer_text)
    return re.sub(r"[ \t]+([.,;])", r"\1", fixed)


    """Rete di sicurezza: corregge nel testo della risposta le citazioni
    abbreviate/troncate, sostituendole con il document_id ESATTO presente
    nei contexts. Non dipende dal fatto che l'LLM segua l'istruzione del
    prompt di copiare l'ID per intero."""
    real_ids = [c["document_id"] for c in contexts]
    real_id_set = set(real_ids)

    def _replace(match: re.Match) -> str:
        candidate = match.group(1)
        if candidate in real_id_set:
            return match.group(0)  # già corretto
        suffix_matches = [
            rid for rid in real_ids
            if rid.endswith("_" + candidate) or rid.endswith(candidate)
        ]
        if len(set(suffix_matches)) == 1:
            return f"[{suffix_matches[0]}]"
        return match.group(0)  # ambiguo o nessun match: lascia invariato

    return re.sub(r"\[([a-zA-Z0-9_\-]+)\]", _replace, answer_text)


def answer_one(
    question: dict,
    *,
    dag,
    embedder,
    vector_store,
    collection: str,
    model: str,
    k: int,
    k_retrieve: int | None = None,
    max_per_doc: int = 1,
) -> dict:
    question_text = question["question"]
    started = time.perf_counter()
    result = naive_rag(dag, collection, question_text, k=k, k_retrieve=k_retrieve)
    latency_ms = max(0, round((time.perf_counter() - started) * 1000))
    hits = result.get("retriever") or dense_retrieve(embedder, vector_store, collection, question_text, k=k_retrieve or k) 
    llm_out = result["llm"]
    answer_text = getattr(llm_out, "text", None) or str(llm_out)
    input_tokens, output_tokens, cached = _token_usage(llm_out)
    contexts = hits_to_contexts(hits, max_contexts=k, max_per_doc=max_per_doc)
    answer_text = fix_citations(answer_text, contexts)
    return {
        "question_id": question["question_id"],
        "question": question_text,
        "answer": answer_text,
        "contexts": contexts,
        "telemetry": {
            "latency_ms": latency_ms,
            "declared_cost_eur": declared_cost_eur(input_tokens, output_tokens, cached),
            "model_calls": [
                {
                    "provider": "openai",
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_input_tokens": cached,
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _require_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY mancante: copiare .env.example → .env e impostare la chiave.")
    return api_key


def run_validate_only(args: argparse.Namespace) -> int:
    payload = json.loads(args.submission.read_text(encoding="utf-8"))
    questions = json.loads(args.questions.read_text(encoding="utf-8")) if args.questions else None
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
    errors = validate_payload(payload, questions, manifest, args.round_id)
    if errors:
        print("VALIDAZIONE FALLITA")
        for error in errors:
            print(f"- {error}")
        return 1
    print("VALIDAZIONE OK - il tentativo ufficiale non e stato consumato")
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Baseline naive RAG (Lab_08) → submission.json")
    parser.add_argument("--questions", type=Path, help="JSON domande del round")
    parser.add_argument("--round-id", help="round_id da scrivere nella submission")
    parser.add_argument("--output", type=Path, help="Percorso submission.json in output")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    parser.add_argument("--qdrant-path", type=Path, default=Path(os.getenv("QDRANT_PATH", "outputs/qdrant")))
    parser.add_argument("--collection", default=os.getenv("COLLECTION_NAME", "caso_dei_mille"))
    parser.add_argument("--k", type=int, default=int(os.getenv("MAX_CONTEXTS", "5")))
    parser.add_argument("--k-retrieve", type=int, default=None, help="Candidati da recuperare prima del dedup (default: max(k*3, 15))")
    parser.add_argument("--max-per-doc", type=int, default=1, help="Max chunk per documento nei contexts finali (diversifica le fonti)")
    parser.add_argument("--rebuild", action="store_true", help="Ricostruisce l'indice Qdrant")
    parser.add_argument("--validate-only", action="store_true", help="Valida una submission già prodotta")
    parser.add_argument("--submission", type=Path, help="Submission da validare (con --validate-only)")
    args = parser.parse_args()

    if not 1 <= args.k <= 5:
        raise SystemExit("--k deve essere tra 1 e 5")

    if args.validate_only:
        if not args.submission:
            raise SystemExit("--validate-only richiede --submission")
        return run_validate_only(args)

    if not args.questions or not args.round_id or not args.output:
        raise SystemExit("Richiesti --questions, --round-id e --output (oppure --validate-only)")

    api_key = _require_api_key()
    model = (
        os.getenv("OPENAI_MODEL")
        or os.getenv("DATAPIZZA_MODEL")
        or "gpt-4o-mini"
    ).strip()

    project_root = Path.cwd()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    files = iter_ingest_files(manifest, project_root)
    if not files:
        raise SystemExit("Nessun documento ingeribile dal manifest")

    embedder, llm = build_clients(api_key, model)
    vector_store = ingest_corpus(
        files=files,
        embedder=embedder,
        qdrant_path=args.qdrant_path,
        collection=args.collection,
        rebuild=args.rebuild,
    )
    dag = build_naive_dag(embedder, vector_store, llm)

    k_retrieve = args.k_retrieve or max(args.k * 3, 15)
    answers = [
        answer_one(
            row,
            dag=dag,
            embedder=embedder,
            vector_store=vector_store,
            collection=args.collection,
            model=model,
            k=args.k,
            k_retrieve=k_retrieve,
            max_per_doc=args.max_per_doc,
        )
        for row in questions["questions"]
    ]
    payload = {"schema_version": "1.0", "round_id": args.round_id, "answers": answers}
    errors = validate_payload(payload, questions, manifest, args.round_id)
    if errors:
        raise SystemExit("Submission non valida:\n- " + "\n- ".join(errors))
    submission = Submission.model_validate(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(submission.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Submission valida: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
