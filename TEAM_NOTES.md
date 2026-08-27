# Team notes

Diario di lavoro del team. **Non è documentazione della baseline** e non entra nello score tecnico /90.

Usatelo come preferite (sezioni libere, link a issue, screenshot). Suggerimenti utili per speech e Historical Review (`pipeline_reflection`):

## Architettura / scelte

Partiamo dalla baseline ufficiale (`baseline_naive_rag.py`) senza riscriverla da zero: Docling → RecursiveSplitter → embedding OpenAI (`text-embedding-3-small`) → Qdrant locale embedded → retrieval denso top-k → generazione con `gpt-4o-mini` e prompt di grounding.

Con Act 4 (propaganda, versioning, fonti contestate) abbiamo sfruttato un campo che il manifest 
aveva già ma la baseline ignorava: `reliability` per documento (valori: propaganda, obsolete, 
corrective, harmful_if_unqualified, contested, allusive, incomplete, ecc.). Ora viene passato 
nei metadata all'ingest e mostrato al modello accanto a ogni brano nel prompt (separato dalla 
citazione, per non rompere il formato `[document_id]`), con istruzione esplicita di qualificare 
la risposta quando la fonte non è neutra e di preferire una versione 'corrective' a una 'obsolete' 
sullo stesso fatto. Scelta deliberata: usare un segnale strutturato già presente nei dati invece 
di sperare che il modello inferisca da solo il tipo di fonte dal solo testo.

Modifiche fatte finora rispetto alla baseline:

1. **Prompt di grounding rafforzato** — citazioni `[document_id]` obbligate in formato pulito, niente testo extra ("Fonte:", "document_id:") prima della parentesi quadra. Vedi tabella fix sotto.
2. **Retrieval con dedup post-processing** — invece di prendere ciecamente i primi 5 risultati Qdrant, recuperiamo più candidati (`k_retrieve = max(k+5, 10)`) e scartiamo i quasi-duplicati (stesso `document_id` + testo contenuto in un chunk già scelto) prima di tagliare al top-5 finale della submission. Non abbiamo toccato il retriever stesso (resta dense-only), solo il post-processing in `hits_to_contexts`.

Trade-off consapevole: il generatore ora vede più chunk grezzi (non deduplicati) perché il dedup avviene solo sul post-processing per la submission, non sul contesto passato all'LLM — costo per domanda leggermente più alto (comunque sotto €0,001), nessun impatto negativo osservato sulla qualità delle risposte.

## Fallimenti e fix

| Round 1 / generazione | Citazione malformata | La risposta a R1_Q003 conteneva `(document_id: [lettera_salina_01])` invece del formato pulito `[lettera_salina_01]` | Il prompt di grounding non specificava il formato esatto della citazione, solo l'obbligo di citare | Rafforzato il prompt: vietato testo davanti alla parentesi quadra, solo `[document_id]` incollato nel testo | Risolto e verificato su tutte le 8 risposte di Round 1 rigenerate |
| Round 1 / retrieval | Contesti quasi-duplicati | R1_Q002 e R1_Q006 avevano 2 dei 5 `contexts` sullo stesso documento (`elenco_personaggi_01`) con testo quasi identico — uno slot su 5 sprecato | Il retrieval naive prendeva sempre e solo i primi k=5 risultati Qdrant senza controllare sovrapposizioni tra chunk dello stesso documento | Recuperati più candidati (`k_retrieve`) e deduplicati per `document_id` + contenimento di sottostringa (dopo normalizzazione spazi/maiuscole) prima di tagliare al top-5 | Risolto e verificato: nessun duplicato residuo su tutte le 8 domande di Round 1 rigenerate |

| Act 4 / prompt | Reliability non sfruttata | Il manifest ha un campo `reliability` per ogni documento (propaganda, obsolete, contested, ecc.) ma la baseline non lo passava né all'indice né al prompt | Il campo esisteva nei dati ma nessuna parte della pipeline lo leggeva | Propagato `reliability` nei metadata di ingestion e reso visibile nel prompt di generazione (tra parentesi tonde, separato dalla citazione) | Da verificare con test mirato su bozza_comitato_palermo_v1 / nota_comitato_palermo_v2 (coppia versione superata / corretta) |
| Act 4 / prompt | Regressione su citazioni e abstention | Modificando `GROUNDING` per aggiungere le istruzioni sulla reliability, la variabile è stata sovrascritta invece che estesa: sparite le regole sul formato citazione e sull'abstention ("Non lo so") già validate in Round 1 | Sostituzione dell'intera variabile invece di un'aggiunta in coda | Ricostruito `GROUNDING` unendo tutte e tre le parti (citazione, reliability, abstention) | Individuato prima di testare su un round vero — nessun impatto sulla submission ufficiale |

## Eval locale / gate pre-submission

Prima di ogni conferma ufficiale:

1. Rigenerare su `eval/sample_questions.json` (veloce, 2 domande) per un primo controllo di fumo dopo qualsiasi modifica al codice.
2. Rigenerare sulle domande vere del round in un file di output separato (es. `outputs/submission_test_*.json`), così non si tocca la submission già confermata mentre si valida un fix.
3. Controllo manuale: citazioni in formato `[document_id]` pulito, nessun contesto quasi-duplicato dello stesso documento, abstention ("Non lo so.") sulle domande a evidenza insufficiente.
4. `--validate-only` per la validazione di schema prima di caricare sulla piattaforma (non consuma il tentativo).


## Debito consapevole prima della Gold

- **Hybrid search (BM25 + dense + RRF)**: non implementato. 
- **Gestione dedicata `garib_*` (OCR rumoroso)**: nessun trattamento specifico oltre a quello che fa Docling di default 
- **Version awareness / propaganda~~: gestito via metadata `reliability` + prompt 
