# Il caso dei Mille — starter RAG

Baseline **naive dense RAG**: un solo file da lanciare end-to-end. Parte subito; a voi renderla seria.

Obiettivo: leggere un archivio ambiguo, recuperare le fonti giuste, rispondere senza inventare certezza. Quando le prove non bastano, vince chi lo dichiara.

Al kickoff scaricate dalla dashboard: `participant_rules` + lo starter della giornata (Act 1–2). Act 3–4 arrivano più tardi come zip progressivi (`install_release.py`).

---

## Mappa della cartella


| Percorso                     | A cosa serve                                                                                 |
| ---------------------------- | -------------------------------------------------------------------------------------------- |
| `baseline_naive_rag.py`      | **Entrypoint**: ingest + naive RAG + `submission.json` (+ validazione)                       |
| `data/act_1/`, `data/act_2/` | Archivio iniziale (file flat). Act 3–4 arrivano come zip durante la giornata                 |
| `data/manifest.json`         | Catalogo obbligatorio: parsing, indice, validazione `document_id`                            |
| `data/checksums.sha256`      | Controlli integrità dei file in `data/`                                                      |
| `data/license_manifest.csv`  | Licenze / URL delle fonti autentiche                                                         |
| `eval/`                      | Domande sample, `submission.schema.json`, `submission.example.json`, annotazioni sample      |
| `scripts/install_release.py` | Installa uno zip di release (Act 3+) e aggiorna il manifest                                  |
| `outputs/`                   | Artefatti locali (Qdrant, submission) — generati da voi, non versionati                      |
| `tests/`                     | Test del contratto di submission; non servono al runtime, ma proteggono schema e validazione |
| `TEAM_NOTES.md`              | Diario del team: gestitelo come preferite                                                    |
| `Makefile`                   | Scorciatoie: `setup`, `sample`, `test`                                                       |


La cartella `tests/` è parte dello starter e non va rimossa: eseguire `make test` dopo modifiche a `baseline_naive_rag.py` o al formato della submission. I test verificano, tra l'altro, i campi non ammessi e la numerazione continua dei contesti.

`manifest.json`: `document_id`, titolo, `act`, `modality`, `origin_type`, `reliability`, `file_records`. I file in `data/` contano solo se sono nel catalogo.

### Scansioni `garib_*`

I file con prefisso `garib_` (es. `garib_d05.pdf`) sono **scansioni autentiche**, non PDF testuali puliti: OCR rumoroso, layout irregolare, pagine come immagine.

Domanda di challenge: **come potresti gestirle** nella pipeline (ingest, OCR/descrizione, chunking, retrieval) senza inventare testo che non c'è?

---



## Setup

Requisiti: **Python 3.11+**, **OPENAI_API_KEY**. Per le scansioni: Tesseract + backend OCR supportato da Docling.

```bash
cd garibaldi-rag-starter
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -U pip
python -m pip install -r requirements.txt
cp .env.example .env               # inserire OPENAI_API_KEY
# opzionale: shasum -c data/checksums.sha256
make sample                        # ingest + submission sample
```

Equivalente senza Make:

```bash
python baseline_naive_rag.py \
  --questions eval/sample_questions.json \
  --round-id sample \
  --output outputs/submission_sample.json \
  --rebuild
```

Solo validare una submission già prodotta (non chiama le API):

```bash
python baseline_naive_rag.py --validate-only \
  --submission outputs/submission_sample.json \
  --questions eval/sample_questions.json \
  --manifest data/manifest.json \
  --round-id sample
```



### Variabili in `.env` (da `.env.example`)

Copiare `.env.example` → `.env`. **Non commitare** `.env`.


| Variabile                | A cosa serve                                                            |
| ------------------------ | ----------------------------------------------------------------------- |
| `OPENAI_API_KEY`         | **Obbligatoria.** Embedding (`text-embedding-3-small`) + LLM            |
| `OPENAI_MODEL`           | Modello di generation (default `gpt-4o-mini`). Alias: `DATAPIZZA_MODEL` |
| `QDRANT_PATH`            | Cartella Qdrant embedded (default `outputs/qdrant`)                     |
| `COLLECTION_NAME`        | Nome collection                                                         |
| `MAX_CONTEXTS`           | Top-k hard (≤ 5, default 5)                                             |
| `DECLARED_COST_EUR`      | (opzionale) forza il costo autodichiarato per domanda                   |
| `COST_*_PER_MILLION_EUR` | (opzionale) stima `declared_cost_eur` da token × rate                   |


---



## La giornata (guida operativa)

```text
Kickoff → Build 1 → Round 1 → Build 2 (+ Act 3)
→ Build 3 (+ Act 4) → Round 2 → Final Sprint → Gold Run
→ Historical Review + speech → consegna repo
```


| Fascia      | Fase                   | Cosa fate voi                                                         |
| ----------- | ---------------------- | --------------------------------------------------------------------- |
| 09:00–09:30 | Kickoff                | Login piattaforma, scaricare starter/materiali, capire schema e score |
| 09:30–10:40 | Build Sprint 1         | Setup, `make sample`, primi fix                                       |
| 10:40–10:55 | **Round 1**            | Una sola submission ufficiale                                         |
| 10:55–12:15 | Build Sprint 2         | Error analysis sul feedback pack; annotare in `TEAM_NOTES.md`         |
| 12:15–13:00 | Act 3                  | Installare gli zip, poi `--rebuild`                                   |
| 13:00–14:00 | Pranzo                 | —                                                                     |
| 14:00–14:35 | Build Sprint 3 + Act 4 | Conflitti, versioni, OCR/mappe, fonti pericolose                      |
| 14:35–14:55 | **Round 2**            | Domande più difficili + feedback                                      |
| 14:55–15:35 | Final Sprint           | Schema, costi, abstention, prompt, regressioni                        |
| 15:45–16:00 | **Gold Run**           | One-shot, domande nascoste, stessi documenti                          |
| 16:00–16:30 | Preparazione presentazioni        | Historical Review + Presentazioni
                                             |
| 16:30–17:30 | Review + speech        | Architettura + HR(5 min + 5 min)
                                             |
| 17:50–18:00 | Premiazione            | Score /110 + badge                                                    |


Round 1/2 = apprendimento (feedback completo). Gold = niente feedback in finestra, un solo tentativo.

---



## Archivio e release pack

Al kickoff: Act 1–2. Poi zip dalla piattaforma (`after_round_1.zip`, `act_4.zip`), da scaricare dalla dashboard team ("Materiali disponibili") e installare uno alla volta, nell'ordine in cui arrivano.

```bash
python scripts/install_release.py ~/Downloads/after_round_1.zip
```

Due errori comuni da evitare:

- la cartella è `scripts/` (plurale) — `script/` non esiste
- il percorso dello zip **non è opzionale**: senza, lo script si ferma con `error: the following arguments are required: archive`. Sostituite l'esempio sopra con dove avete davvero scaricato il file.

Poi rigenerate la submission con `--rebuild`, perché l'indice includa i documenti appena installati:

```bash
python baseline_naive_rag.py \
  --questions questions_round_1.json \
  --round-id round_1 \
  --output outputs/submission.json \
  --rebuild
```

`install_release.py` copia i file sotto `data/` (crea `act_3/`… se servono) e fonde il manifest. Se il numero di Act non cresce, fermatevi e controllate lo zip.

⚠️ `--rebuild` **è tutto o niente:** cancella l'intera collection Qdrant e la ricostruisce da zero — quindi ri-passa da Docling e ri-embedda **anche** i documenti degli Act già indicizzati, non solo quelli nuovi. È voluto (baseline volutamente naive): il tempo/costo del rebuild cresce con la dimensione totale dell'archivio, non con quanto è cambiato. Migliorarlo con un'ingestion incrementale è tra i miglioramenti che potete costruire voi.

Progressione tipica:


| Momento      | Documenti         | Focus tecnico                                       |
| ------------ | ----------------- | --------------------------------------------------- |
| Kickoff      | Act 1–2           | RAG testuale, punti di vista, contraddizioni        |
| Dopo Round 1 | + Act 3 | Cross-doc, tabelle, prime mappe/scan                |
| Dopo pranzo  | + Act 4           | Versioning, propaganda, fonti dannose se usate male |
| Gold         | Stesso archivio   | Domande nuove                                       |


---



## Submission (tutti i round)

Le domande ufficiali **non sono nel repo**: arrivano come zip (`round_1_questions.zip`, `round_2_questions.zip`, `gold_questions.zip`). Prima usate `eval/sample_questions.json`.

`--rebuild` **ricostruisce l'indice Qdrant, non è legato alle domande.** Se avete già lanciato `make sample` (o il baseline) sulle stesse Act 1–2, l'indice esiste già: **non serve rimetterlo** per Round 1, viene riusato così com'è (più veloce, salta di nuovo tutto il parsing/embedding). Aggiungetelo di nuovo **solo** dopo aver installato un release nuovo (Act 3/4/5 via `install_release.py`) — altrimenti l'indice resta fermo ai documenti vecchi senza errore visibile.

```bash
python baseline_naive_rag.py \
  --questions questions_round_1.json \
  --round-id round_1 \
  --output submission.json
  # aggiungete --rebuild solo se avete appena installato un release (Act 3+)
```

Il comando genera **e** valida. Rivalidare senza rigenerare: `--validate-only` (vedi Setup).

### Forma del file da caricare

Partite da questo esempio (stessa struttura che deve avere il file caricato):

- **Esempio (forma da copiare):** `[eval/submission.example.json](eval/submission.example.json)`
- **Schema (regole formali, stessi campi):** `[eval/submission.schema.json](eval/submission.schema.json)`

Sono allineati: l’example mostra un JSON valido; lo schema elenca tipi, obbligatorietà e limiti (`max 5` contesti, niente campi extra). La validazione eseguibile è in `baseline_naive_rag.py`.

Campi:

- `schema_version` (`"1.0"`), `round_id`
- `answers[]`: `question_id`, `question` (testo), `answer`, `contexts` (0–5), `telemetry`
- ogni contesto: `rank` 1..N, `document_id`, `content`
- telemetria: `latency_ms`, `declared_cost_eur`, `model_calls[]` (`provider`, `model`, token)

**Vietati:** `chunk_id`, `modality`, `page`, `location_label`, e top-level `confidence` / `answer_type` / `estimated_cost` / `commit_hash` / `repository_url`.

**Contesti = top-5 hard pre-generation:** ciò che entra nel modello, nello stesso ordine. Mappe/scan: citare quel `document_id` (`content` = descrizione/OCR). Contesto non supportato dal corpus → Faithfulness a zero su quella domanda.

Validare sul sito **non** consuma il tentativo. Solo **Conferma irreversibile** conta. Una submission ufficiale per round.

---



## Score (110 punti)


| Blocco            | Punti | Cosa misura                                            |
| ----------------- | ----- | ------------------------------------------------------ |
| Retrieval         | 40    | Recall@5, Precision@up-to-5, Hit@5, Rank Quality       |
| Generation        | 35    | Faithfulness, Answer Correctness, Historical Reasoning |
| Latency & Cost    | 15    | Wall-clock + `declared_cost_eur`                       |
| Historical Review | 10    | Tesi umana dopo la Gold                                |
| Speaker           | 10    | Valutazione dello speech                               |


JSON non conforme allo schema (o domande incomplete) → zero / rifiuto in validazione. Fasce latency/cost: `docs/SCORING_AND_FAIRNESS.md` nel monorepo (o pack regole).

Per un controllo manuale sul sample: confrontate i `document_id` in `outputs/submission_sample.json` con `eval/sample_annotations.json`. Scoring ufficiale = piattaforma. 

---



## Cosa lascia aperta la baseline

Hybrid search, rerank, query rewrite, chunking semantico, OCR/mappe, version awareness, affidabilità fonti, abstention, self-check, costi/latency, ingestion incrementale (oggi `--rebuild` ri-processa tutto l'archivio, non solo i documenti nuovi). Partite da `baseline_naive_rag.py` e miglioratela.

---



## Note di team

Un solo file: `[TEAM_NOTES.md](TEAM_NOTES.md)`. Usatelo come volete (architettura, failure log, checklist eval). Non entra nello score tecnico; serve a voi e allo speech della Historical Review.

---



## Dopo la Gold: Historical Review + speech

Presentazione orale ~10 min sulla domanda assegnata e su architettura → cosa non funzionava → fonti decisive → tesi storica. La giuria registra, trascrive e valuta; **non** consegnate un JSON di Historical Review. Il punteggio si somma alla Gold Run.

### Consegna repo

- codice + test
- `TEAM_NOTES.md` (e altra docs se vi serve)
- `outputs/submission_gold.json`
- URL GitHub / branch / tag **sulla piattaforma**, mai dentro `submission.json`

---

Una RAG non è buona perché parla bene: lo è quando trova le carte giuste, le usa con misura e sa fermarsi davanti all’incertezza.