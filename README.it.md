[English](README.md)

# email-triage-eval

Un programma da riga di comando che smista con l'API di Claude le email dei clienti che arrivano in una casella condivisa. Per ogni email propone categoria, urgenza, reparto, una bozza di risposta e una riga che spiega il perché. Non spedisce niente: le bozze finiscono in un file e le guarda una persona.

Claude legge l'email e ne tira fuori i fatti. Cosa è urgente, a chi tocca e se la bozza può partire lo decide invece il codice Python, con meno di 200 righe di regole. Con un comando di eval si misura tutto su un gold set che ho etichettato a mano.

## La casella di un noleggio di bici

La prima versione l'ho scritta per una prova pratica durante una selezione di lavoro. Questa è una riscrittura per il portfolio, con azienda, email, gold set e prompt nuovi e inventati.

L'azienda si chiama Larkspur Cycle Works: noleggia e ripara bici, e non esiste. Nella sua casella arriva di tutto, dai cambi di noleggio ai guasti per strada, dalle prenotazioni in officina ai preventivi per gruppi, e poi fatture di fornitori, reclami, risposte automatiche e phishing. Chi la apre la mattina cerca prima di tutto il ciclista rimasto a piedi, poi gira il resto al reparto giusto. Ogni risposta deve evitare di promettere prezzi, tempi o rimborsi, perché può farlo solo un responsabile. Le bici non hanno un motivo particolare: l'originale era una prova delle mie competenze, e all'azienda bastava essere inventata.

Con le email scritte male Claude se la cava bene. Però regole come "a un fornitore non si risponde dalla casella condivisa", "niente bozza finché non sappiamo di che bici si parla" o "il secondo sollecito sale di un livello di urgenza" non volevo affidarle a lui. Devono valere ogni volta, quindi le ho messe nel codice, con i loro test.

## Una chiamata al modello per email, poi le regole nel codice

```text
data/inbox.json
     |
     v
signals.py   regex and calendar: reply depth, chaser phrases, money, bounces,
     |       "Wednesday 14 May" when 14 May is a Thursday
     v
threads.py   same sender -> RELATED or FOLLOW_UP; a bounce is linked by the subject it quotes
     |
     +-- bounce? --yes--> rules.decide_bounce(): inherit routing from the original, no model call
     |
     no
     v
llm.py       ONE call per email: client.messages.parse(output_format=Extraction)
     |         system prompt (company profile + field definitions), cache_control: ephemeral
     |         disk cache keyed by sha256(model, effort, system, schema, message)
     |         usage and cost logged per call
     v
rules.py     safety and escalation floors, follow-up bump, team matrix,
     |       intake merged from the thread, then the draft gate:
     |       SYSTEM/bounce -> IGNORE -> SUPPLIER -> missing intake -> house-rule validators -> READY
     v
output/<run>/results.json, results.md, costs.md
     |
     v
evaluate.py  compare with eval/gold_dev.json and eval/gold_holdout.json, field by field
```

| File | Cosa fa |
|---|---|
| `triage/models.py` | `Extraction`, l'unico schema che compila il modello, più i miei `Signals`, `Thread`, `Result` |
| `triage/llm.py` | L'unico punto che chiama Claude: output strutturato, prompt caching, cache su disco, un nuovo tentativo se lo schema non torna, gestione dei rifiuti, costo |
| `triage/rules.py` | Le regole interne e il gate sulla bozza |
| `triage/signals.py`, `triage/threads.py` | Il lavoro preliminare, tutto in codice e senza modello |
| `triage/fake.py` | Finto modello a parole chiave, offline, con la stessa firma `messages.parse` dell'SDK |
| `triage/evaluate.py` | Confronto con il gold, con alternative "accettabili" che devono combaciare su tutti i campi |
| `triage/prompts/` | Prompt di sistema, profilo dell'azienda, messaggio per ogni email |

## Le scelte che ho fatto

1. Allo schema chiedo dei fatti (`safety_risk`, `escalation_threat`, i tre campi di intake, il tipo di mittente) e una proposta di categoria, urgenza, reparto e bozza. Poi `rules.py` applica le soglie minime e il gate, e scrive ogni correzione in `meta.corrections`. Nel prompt le regole non le ho messe. Nel progetto originale, rifacendo un'esecuzione senza controllare la temperatura, sono cambiati 2 valori di smistamento su 100 e il testo di quasi tutte le bozze. Con le regole nel codice la decisione è sempre la stessa, e posso testarla senza rete.

2. Nessuna regola dipende da un'etichetta scelta dal modello. Nell'originale il gate esentava un tipo di richiesta dal controllo dell'intake, ma quel tipo lo sceglieva il modello, e alla prima eval una riga sbagliata è venuta proprio da lì. Qui il controllo vale per tutte le email REPAIR e RENTAL, nessuna esclusa.

3. Certe cose un modello le fa male, e allora le calcola il codice: collegare i thread, riconoscere i bounce, trovare il refuso nell'indirizzo rimbalzato, accorgersi che giorno della settimana e data non tornano. Se rimbalza una nostra risposta, il bounce non passa dal modello e prende lo smistamento dell'email a cui si rispondeva. Così le chiamate sono una per email, bounce esclusi. In questa copia un thread è solo "stesso mittente".

4. Le cache sono due. Il prompt di sistema non cambia di un byte da una chiamata all'altra e ha `cache_control: {"type": "ephemeral"}`, quindi dalla seconda email in poi Anthropic lo prende dalla sua prompt cache. Sono circa 1.400 token, stimati contando i caratteri e dividendo per 4: abbastanza per entrare in cache. Con l'API non li ho ancora contati. Poi c'è la cache su disco: ogni risposta completa viene salvata con una chiave sha256 di modello, effort, prompt di sistema, schema e messaggio, quindi rifare un'esecuzione, usare `--offline` o lanciare i test non costa niente. La cache su disco permette anche a chiunque di riprodurre un risultato senza una chiave API.

5. Di default il modello è `claude-opus-5-5`, e con `TRIAGE_MODEL` lo si cambia. Su questo modello il thinking non si può spegnere, quindi regolo quanto ragiona con `output_config={"effort": "low"}` (modificabile con `TRIAGE_EFFORT`). `max_tokens` è a 8000 perché anche il thinking conta nel limite. L'originale girava su `claude-sonnet-5` con `thinking={"type": "disabled"}`, che su `claude-opus-5-5` dà un errore 400.

6. Volevo che ogni errore si vedesse nell'output.

   Cosa succede quando qualcosa va storto:

   - Gli errori temporanei dell'API li ritenta l'SDK da solo.
   - Una risposta che non rispetta lo schema o si ferma a `max_tokens` ha un secondo tentativo. Se l'SDK riesce comunque a restituirla, i suoi token finiscono nel consumo dell'email. Se invece l'SDK scarta il JSON dentro `parse()`, il consumo non si può leggere: la chiamata viene contata come "unmetered" e `costs.md` lo dice.
   - Un `refusal` non si ritenta.
   - Anche una `ANTHROPIC_API_KEY` mancante è un errore: il client si costruisce solo da quella variabile, così l'SDK non può ripiegare su altre credenziali che trova sul computer.
   - Qualsiasi errore segna l'email come "review manually", e l'esecuzione va avanti.

   Tutti questi casi li ho testati sull'SDK vero, simulando il trasporto HTTP.

Una cosa invece l'ho sbagliata. Nell'originale ho migliorato il prompt guardando le stesse email che usavo per misurarlo. Il giorno 1 sono passato da 23 righe esatte su 25 a 25 su 25 dopo una modifica al prompt, quindi quel 25 su 25 dice poco. Oggi terrei da parte fin dall'inizio un gruppo di email che non guardo mai mentre lavoro sul prompt. In questa copia lo fa la divisione tra dev e holdout.

## Provarlo in 5 minuti

Ti serve Python 3.10 o più recente.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest                      # 95 tests, all offline
```

### Offline, senza chiave

```powershell
python -m triage demo
```

Fa girare tutta la pipeline sulle 23 email, con un finto modello a parole chiave al posto di Claude, e poi misura il risultato su entrambe le parti del gold set. Scrive `output/demo/results.md`, `costs.md`, `eval-dev.md` e `eval-holdout.md`. Ogni report di questo comando ha `SIMULATED` scritto in cima. Il finto modello non scrive mai nella cache su disco, così più avanti nessuno può scambiare le sue risposte per quelle di Claude.

### Con una chiave

```powershell
copy .env.example .env                # then set ANTHROPIC_API_KEY in .env
python -m triage run --out output/live
python -m triage eval --results output/live/results.json --gold eval/gold_dev.json --out output/live/eval-dev.md
python -m triage eval --results output/live/results.json --gold eval/gold_holdout.json --out output/live/eval-holdout.md
python -m triage run --out output/live --offline   # replay from cache/, no API call
```

La chiave viene letta solo dalle variabili d'ambiente. `.env`, `cache/` e `output/` sono in `.gitignore`.

## L'eval

In `data/inbox.json` ci sono 23 email sintetiche, distribuite su due giorni e scritte apposta. Di ogni mittente c'è solo il nome visualizzato e un dominio sotto il TLD riservato `.example`, quindi nel repository non compare nessun indirizzo email.

Il gold set l'ho diviso prima di mettere mano al prompt. Su `eval/gold_dev.json` (12 email) si lavora per migliorare il prompt, mentre `eval/gold_holdout.json` (11 email) dà il numero da riportare. Nel progetto originale avevo iterato su tutto il gold set, ed è soprattutto per questo che il suo risultato migliore è ottimista.

Per ogni email il gold indica categoria, urgenza, reparto, stato della bozza e collegamento al thread attesi, ed eventuali alternative `acceptable`. Un'alternativa vale solo se combaciano tutti i suoi campi: dove ci si aspetta "COMPLAINT per MANAGEMENT" può andare bene "REPAIR per WORKSHOP", ma non "REPAIR per MANAGEMENT". Il report guarda anche i segnali di calendario e di bounce, e conta le bozze READY che contengono un prezzo o una promessa sui tempi (devono essere 0).

## Risultati

### Questa copia: mai eseguita con Claude

Con questo codice, questo dataset e questi prompt non ho ancora fatto nemmeno una chiamata all'API di Claude. I numeri qui sotto vengono dal finto modello a parole chiave, offline, e servono solo a dimostrare che pipeline, regole ed eval girano dall'inizio alla fine. Su quanto è preciso Claude non dicono niente. E sono ottimisti, perché le parole chiave e il dataset li ho scritti io.

| Parte | Accettabili | Esatte (4 campi) | Fonte |
|---|---|---|---|
| dev (12) | 11/12 | 10/12 | `output/demo/eval-dev.md`, finto modello a parole chiave |
| holdout (11) | 9/11 | 8/11 | `output/demo/eval-holdout.md`, finto modello a parole chiave |

### Esecuzione originale, su un altro dataset

Questi numeri vengono dal progetto originale, con un'altra azienda inventata e un dataset di 33 email che non ho pubblicato. Il modello era `claude-sonnet-5`, non `claude-opus-5-5` come qui, e lo chiamavo attraverso un gateway di terzi compatibile con il protocollo di Anthropic.

| Misura | Valore |
|---|---|
| Giorno 1, 25 email, prima versione del prompt | 23/25 righe esatte |
| Giorno 1, dopo una modifica generale al prompt fatta guardando il gold | 25/25 |
| Giorno 2, 8 email che il prompt non aveva mai visto | 8/8 |
| Bozze con importi o riferimenti a tempi | 0 in entrambi i giorni |
| Costo delle due esecuzioni salvate nel repository | 0,25 USD |
| Spesa totale, con le prove sul prompt e un test di varianza | 0,59 USD |
| Costo per email con il prompt di sistema in cache | circa 0,005-0,008 USD |
| Latenza | circa 5,5 s per email |
| Seconda esecuzione senza cache su disco | 2 valori di smistamento cambiati su 100 |

Di quei numeri mi fido solo dell'8/8 sulle email che il prompt non aveva mai visto. E 8 email sono poche.

## Cosa manca in questa copia

Mancano il dataset originale (33 email), il suo gold set e i suoi prompt. Manca anche la chiamata in più al modello che l'originale faceva per confermare le coppie di thread dubbie.

## Limiti

- I dati sono tutti sintetici: su una casella vera non ho misurato niente.
- Non c'è un CRM, quindi un cliente che conosciamo e uno nuovo sembrano uguali, e se manca il codice di prenotazione nessuno lo recupera.
- Se un'email contiene due richieste, va comunque a un solo reparto. La seconda resta in `secondary_requests`, ma nessuno la smista.
- La regola sull'intake e l'urgenza vanno in direzioni opposte: un ciclista rimasto a piedi che non dà il codice di prenotazione non riceve una risposta scritta, solo un'azione "chiamalo".
- Un thread è solo "stesso mittente". Un collega che scrive dalla stessa azienda sullo stesso caso apre un thread nuovo.
- Sulle bozze ci sono solo controlli con regex per prezzi, promesse sui tempi e parole sui rimborsi. Se il tono è giusto e se la risposta è corretta lo deve ancora dire una persona.
- I fallback lato server sui rifiuti (una beta per `claude-opus-5-5`) non sono attivi. Un rifiuto viene segnalato e l'email va in revisione manuale.
- La tabella dei prezzi in `triage/config.py` l'ho copiata a mano. Prima di fidarti di un report sui costi controllala sulla pagina dei prezzi di Anthropic.

## Licenza

MIT. Vedi [LICENSE](LICENSE). Autore: Lorenzo Algeri.
