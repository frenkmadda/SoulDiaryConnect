from django.shortcuts import render, redirect, get_object_or_404
from .models import Medico, Paziente, NotaDiario, RiassuntoCasoClinico
from django.contrib import messages
from django.contrib.auth import logout
from django.utils import timezone
from django.http import JsonResponse
from django.core.cache import cache
from django.views.decorators.http import require_http_methods
import requests
import logging
import re
import json
import hashlib
import difflib

logger = logging.getLogger(__name__)

# Configurazione Ollama
OLLAMA_BASE_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1:8b"  # Cambia in "cbt-assistant" se hai creato il modello personalizzato

# Configurazione lunghezza note cliniche (in caratteri)
LUNGHEZZA_NOTA_BREVE = 250
LUNGHEZZA_NOTA_LUNGA = 500


def genera_con_ollama(prompt, max_chars=None, temperature=0.7):
    """
    Funzione helper per chiamare Ollama API e normalizzare la risposta rimuovendo
    eventuali prefissi o etichette introduttive (es. "Risposta:", "La tua risposta:").
    
    Args:
        prompt: Il prompt da inviare al modello
        max_chars: Numero massimo di caratteri per la risposta (opzionale)
        temperature: Temperatura per la generazione (default 0.7)
    """
    try:
        # Stima approssimativa: ~2 caratteri per token in italiano
        # Aggiungiamo un margine di sicurezza per evitare troncamenti
        estimated_tokens = (max_chars * 2) if max_chars else 500
        
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": estimated_tokens,
            }
        }

        response = requests.post(OLLAMA_BASE_URL, json=payload, timeout=500)
        
        # Log della risposta per debug
        if response.status_code != 200:
            logger.error(f"Ollama ha restituito status code {response.status_code}")
            logger.error(f"Risposta: {response.text}")
            return "Il servizio di generazione testo non è al momento disponibile. Riprova più tardi."
        
        response.raise_for_status()
        result = response.json()

        # Estrai il testo dalla risposta in modo robusto
        text = ''
        if isinstance(result, dict):
            # Ollama può restituire diversi formati; proviamo alcune chiavi comuni
            for key in ('response', 'text', 'output', 'result'):
                if key in result and result[key]:
                    text = result[key]
                    break
        else:
            text = result

        # Se il testo è una lista, unisci gli elementi
        if isinstance(text, list):
            text = " ".join(map(str, text))

        text = str(text or '').strip()

        # Rimuove prefissi introduttivi comuni (case-insensitive)
        text = re.sub(
            r'^\s*(?:La tua risposta[:\-\s]*|Risposta[:\-\s]*|Output[:\-\s]*|>\s*|Answer[:\-\s]*|Risposta del modello[:\-\s]*)+',
            '',
            text,
            flags=re.I
        )

        # Rimuove frasi introduttive tipiche delle note cliniche
        text = re.sub(
            r'^\s*(?:Ecco la (?:nota clinica|valutazione|analisi)[:\-\s]*|Di seguito[:\-\s]*|La valutazione è[:\-\s]*|Ecco l\'analisi[:\-\s]*|Nota clinica[:\-\s]*)+',
            '',
            text,
            flags=re.I
        )

        # Rimuove virgolette, apici, bullets o caratteri di maggiore iniziali
        text = re.sub(r'^[\'"«\s\-\u2022>]+', '', text).strip()

        return text if text else "Generazione non disponibile al momento."

    except requests.exceptions.ConnectionError:
        logger.error("Impossibile connettersi a Ollama. Assicurati che il servizio sia in esecuzione.")
        return "Servizio di generazione testo non disponibile. Verifica che Ollama sia attivo."
    except requests.exceptions.Timeout:
        logger.error("Timeout nella chiamata a Ollama")
        return "Il tempo di attesa per la generazione è scaduto. Riprova."
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore nella chiamata a Ollama: {e}")
        return "Errore durante la generazione del testo. Riprova più tardi."
    except Exception as e:
        logger.error(f"Errore imprevisto: {e}")
        return "Errore imprevisto durante la generazione. Riprova."


def home(request):
    return render(request, 'SoulDiaryConnectApp/home.html')


def login_view(request):
    if request.method == 'POST':
        email = request.POST['email']
        password = request.POST['password']

        medico = Medico.objects.filter(email=email, password=password).first()
        paziente = Paziente.objects.filter(email=email, password=password).first()

        if medico:
            request.session['user_type'] = 'medico'
            request.session['user_id'] = medico.codice_identificativo
            next_url = request.GET.get('next', 'medico_home')
            print(f"Redirecting to: {next_url}")
            return redirect(next_url)
        elif paziente:
            request.session['user_type'] = 'paziente'
            request.session['user_id'] = paziente.codice_fiscale
            next_url = request.GET.get('next', 'paziente_home')
            print(f"Redirecting to: {next_url}")
            return redirect(next_url)
        else:
            print("Login fallito")
            messages.error(request, 'Email o password non validi.')

    return render(request, 'SoulDiaryConnectApp/login.html')


def register_view(request):
    if request.method == 'POST':
        user_type = request.POST['user_type']

        nome = request.POST['nome']
        cognome = request.POST['cognome']
        email = request.POST['email']
        password = request.POST['password']

        if user_type == 'medico':
            # Generazione automatica del codice identificativo
            ultimo_medico = Medico.objects.all().order_by('-codice_identificativo').first()
            if ultimo_medico:
                try:
                    ultimo_codice = int(ultimo_medico.codice_identificativo)
                    nuovo_codice = str(ultimo_codice + 1)
                except ValueError:
                    # Se il codice esistente non è numerico, inizia da 1
                    nuovo_codice = '1'
            else:
                # Primo medico, inizia da 1
                nuovo_codice = '1'
            
            indirizzo_studio = request.POST['indirizzo_studio']
            citta = request.POST['citta']
            numero_civico = request.POST['numero_civico']
            numero_telefono_studio = request.POST.get('numero_telefono_studio')
            numero_telefono_cellulare = request.POST.get('numero_telefono_cellulare')

            Medico.objects.create(
                codice_identificativo=nuovo_codice,
                nome=nome,
                cognome=cognome,
                indirizzo_studio=indirizzo_studio,
                citta=citta,
                numero_civico=numero_civico,
                numero_telefono_studio=numero_telefono_studio,
                numero_telefono_cellulare=numero_telefono_cellulare,
                email=email,
                password=password,
            )
        elif user_type == 'paziente':
            # Dettagli specifici per il paziente
            codice_fiscale = request.POST['codice_fiscale']
            data_di_nascita = request.POST['data_di_nascita']
            med = request.POST['med']

            # Creazione del paziente
            Paziente.objects.create(
                codice_fiscale=codice_fiscale,
                nome=nome,
                cognome=cognome,
                data_di_nascita=data_di_nascita,
                med=Medico.objects.get(codice_identificativo=med),
                email=email,
                password=password,
            )

        messages.success(request, 'Registrazione completata con successo!')
        return redirect('login')

    # Recupera tutti i medici per il dropdown
    medici = Medico.objects.all().order_by('cognome', 'nome')
    return render(request, 'SoulDiaryConnectApp/register.html', {'medici': medici})


def logout_view(request):
    logout(request)
    return redirect('login')


def medico_home(request):
    if request.session.get('user_type') != 'medico':
        return redirect('/login/')

    medico_id = request.session.get('user_id')
    medico = get_object_or_404(Medico, codice_identificativo=medico_id)

    # Lista dei pazienti
    pazienti = Paziente.objects.filter(med=medico)

    # Paziente selezionato
    paziente_id = request.GET.get('paziente_id')
    paziente_selezionato = Paziente.objects.filter(codice_fiscale=paziente_id).first()

    # Note del paziente selezionato
    note_diario = NotaDiario.objects.filter(paz=paziente_selezionato).order_by('-data_nota') if paziente_selezionato else None

    # Aggiungi le emoji alle note per il template
    if note_diario:
        for nota in note_diario:
            nota.emoji = get_emoji_for_emotion(nota.emozione_predominante)
            nota.emotion_category = get_emotion_category(nota.emozione_predominante)

    return render(request, 'SoulDiaryConnectApp/medico_home.html', {
        'medico': medico,
        'pazienti': pazienti,
        'paziente_selezionato': paziente_selezionato,
        'note_diario': note_diario,
    })


def genera_frasi_di_supporto(testo):
    """
    Genera frasi di supporto empatico per il paziente usando Ollama
    """
    print("Generazione frasi supporto con Ollama")

    prompt = f"""Sei un assistente empatico e di supporto emotivo. Il tuo compito è rispondere con calore e comprensione a persone che stanno attraversando momenti difficili.

Esempio:
Testo del paziente: "Ho fallito il mio esame e ho voglia di arrendermi."
Risposta di supporto: "Mi dispiace molto per il tuo esame. È normale sentirsi delusi, ma questo non definisce il tuo valore come persona. Potresti provare a rivedere il tuo metodo di studio e chiedere aiuto se ne hai bisogno. Ce la puoi fare!"

ISTRUZIONI:
- Rispondi in italiano con tono caldo, empatico e incoraggiante
- Riconosci e valida le emozioni espresse
- Offri una prospettiva positiva senza minimizzare i sentimenti
- Suggerisci delicatamente possibili strategie o riflessioni utili
- Non usare un tono clinico o distaccato
- Completa sempre la risposta, non troncare mai a metà

Testo del paziente:
{testo}

Rispondi con una frase di supporto:"""

    return genera_con_ollama(prompt, max_chars=500, temperature=0.3)


# Dizionario delle emozioni con le relative emoji
EMOZIONI_EMOJI = {
    'gioia': '😊',
    'felicità': '😄',
    'tristezza': '😢',
    'rabbia': '😠',
    'paura': '😨',
    'ansia': '😰',
    'sorpresa': '😲',
    'disgusto': '🤢',
    'vergogna': '😳',
    'colpa': '😔',
    'frustrazione': '😤',
    'speranza': '🌟',
    'gratitudine': '🙏',
    'amore': '❤️',
    'solitudine': '😞',
    'confusione': '😕',
    'stanchezza': '😩',
    'serenità': '😌',
    'nostalgia': '🥺',
    'delusione': '😞',
    'entusiasmo': '🤩',
    'preoccupazione': '😟',
    'calma': '😊',
    'nervosismo': '😬',
    'malinconia': '🥀',
    'inadeguatezza': '😔',
    'disperazione': '😰',
    'orgoglio': '😌',
    'imbarazzo': '😳',
}

# Categorie delle emozioni per colorazione
EMOZIONI_CATEGORIE = {
    # Emozioni positive (verde)
    'gioia': 'positive',
    'felicità': 'positive',
    'speranza': 'positive',
    'gratitudine': 'positive',
    'amore': 'positive',
    'serenità': 'positive',
    'entusiasmo': 'positive',
    'calma': 'positive',
    'orgoglio': 'positive',
    # Emozioni negative (rosso)
    'tristezza': 'negative',
    'rabbia': 'negative',
    'paura': 'negative',
    'disgusto': 'negative',
    'frustrazione': 'negative',
    'solitudine': 'negative',
    'delusione': 'negative',
    'malinconia': 'negative',
    'disperazione': 'negative',
    # Emozioni ansiose (giallo/ambra)
    'ansia': 'anxious',
    'preoccupazione': 'anxious',
    'nervosismo': 'anxious',
    'stanchezza': 'anxious',
    # Emozioni neutre (lilla)
    'sorpresa': 'neutral',
    'vergogna': 'neutral',
    'colpa': 'neutral',
    'confusione': 'neutral',
    'nostalgia': 'neutral',
    'inadeguatezza': 'neutral',
    'imbarazzo': 'neutral',
}


def get_emotion_category(emozione):
    """
    Restituisce la categoria dell'emozione per la colorazione CSS.
    """
    if not emozione:
        return 'neutral'
    emozione_lower = emozione.lower().strip()
    return EMOZIONI_CATEGORIE.get(emozione_lower, 'neutral')


def analizza_sentiment(testo):
    """
    Analizza il sentiment del testo del paziente e restituisce l'emozione predominante
    con relativa spiegazione.

    Returns:
        tuple: (emozione, spiegazione)
    """
    print("Analisi sentiment con Ollama")

    emozioni_lista = ', '.join(EMOZIONI_EMOJI.keys())
    
    prompt = f"""Sei un esperto di analisi delle emozioni. Il tuo compito è identificare l'emozione predominante in un testo e spiegare perché.

EMOZIONI DISPONIBILI (scegli SOLO tra queste):
{emozioni_lista}

FORMATO RISPOSTA (OBBLIGATORIO):
Emozione: [una sola parola dalla lista]
Spiegazione: [breve spiegazione di 1-2 frasi che cita elementi specifici del testo]

REGOLE FONDAMENTALI:
1. La prima riga DEVE iniziare con "Emozione:" seguita da UNA SOLA PAROLA dalla lista
2. La seconda riga DEVE iniziare con "Spiegazione:" seguita dalla motivazione
3. Nella spiegazione, cita parole o frasi SPECIFICHE del testo originale
4. La spiegazione deve essere breve (max 2 frasi)
5. NON inventare emozioni non presenti nella lista

ESEMPI CORRETTI:
Testo: "Oggi sono riuscito a superare l'esame, sono contentissimo e felice!"
Emozione: felicità
Spiegazione: Il testo esprime felicità attraverso termini positivi come "contentissimo" e "felice", inoltre il successo nell'esame indica un evento gratificante.

Testo: "Mi sento solo e nessuno mi capisce, è terribile"
Emozione: solitudine
Spiegazione: L'espressione "mi sento solo" e "nessuno mi capisce" indica chiaramente un vissuto di isolamento e mancanza di connessione con gli altri.

Testo: "Non ce la faccio più, tutto va storto e sono stufo"
Emozione: frustrazione
Spiegazione: Le frasi "non ce la faccio più" e "tutto va storto" indicano un accumulo di difficoltà che genera un senso di impotenza e irritazione.

Testo da analizzare:
{testo}

Rispondi ora nel formato richiesto:"""

    risposta = genera_con_ollama(prompt, max_chars=300, temperature=0.2)

    # Parsing della risposta
    linee = risposta.strip().split('\n')
    emozione = None
    spiegazione = None

    for linea in linee:
        linea_stripped = linea.strip()
        if linea_stripped.lower().startswith('emozione:'):
            emozione = linea_stripped.split(':', 1)[1].strip().lower().rstrip('.!?,;:')
        elif linea_stripped.lower().startswith('spiegazione:'):
            spiegazione = linea_stripped.split(':', 1)[1].strip()

    # Validazione e normalizzazione dell'emozione
    if emozione and emozione in EMOZIONI_EMOJI:
        emozione_validata = emozione
    else:
        # Fallback con la logica esistente di fuzzy matching
        emozione_validata = 'confusione'
        for chiave in EMOZIONI_EMOJI.keys():
            if emozione and chiave in emozione:
                emozione_validata = chiave
                break

        # Controllo sinonimi
        sinonimi = {
            'contentezza': 'gioia',
            'allegria': 'gioia',
            'contento': 'gioia',
            'felice': 'felicità',
            'triste': 'tristezza',
            'arrabbiato': 'rabbia',
            'furioso': 'rabbia',
            'spaventato': 'paura',
            'impaurito': 'paura',
            'ansioso': 'ansia',
            'agitato': 'ansia',
            'nervoso': 'nervosismo',
            'stanco': 'stanchezza',
            'affaticato': 'stanchezza',
            'angoscia': 'ansia',
            'angosciato': 'ansia',
            'confuso': 'confusione',
            'nostalgico': 'nostalgia',
            'deluso': 'delusione',
            'solo': 'solitudine',
            'isolato': 'solitudine',
            'frustrato': 'frustrazione',
            'orgoglioso': 'orgoglio',
            'imbarazzato': 'imbarazzo',
            'inadeguato': 'inadeguatezza',
            'disperato': 'disperazione',
        }

        if emozione and emozione in sinonimi:
            emozione_validata = sinonimi[emozione]

    if not spiegazione:
        spiegazione = "Emozione rilevata in base al contenuto generale del testo."

    print(f"Emozione rilevata: {emozione_validata}, Spiegazione: {spiegazione}")

    return emozione_validata, spiegazione


def get_emoji_for_emotion(emozione):
    """
    Restituisce l'emoji corrispondente all'emozione.
    Se l'emozione non è nel dizionario, restituisce un'emoji di default.
    """
    if not emozione:
        return '💭'
    emozione_lower = emozione.lower().strip()
    return EMOZIONI_EMOJI.get(emozione_lower, '💭')


def _genera_prompt_strutturato_breve(testo, parametri_strutturati, tipo_parametri, max_chars, contesto_precedente):
    """Prompt per nota strutturata breve"""
    return f"""Sei un assistente per uno psicoterapeuta. Analizza il seguente testo e fornisci una valutazione clinica strutturata e CONCISA.

CONTESTO - Note precedenti del paziente (SOLO per riferimento, NON descrivere ogni nota):
{contesto_precedente}

Esempio:
Testo: "Oggi ho fallito il mio esame e ho voglia di arrendermi."
Risposta:
{parametri_strutturati}

Parametri da utilizzare:
{tipo_parametri}

ISTRUZIONI FONDAMENTALI:
- La risposta deve essere BREVE e SINTETICA (massimo {max_chars} caratteri)
- FORMATO OBBLIGATORIO: ogni parametro deve essere su una NUOVA RIGA nel formato "NomeParametro: valore"
- Vai a capo dopo ogni parametro

REGOLE PER L'ANALISI:
1. CONCENTRATI AL 90% SULLA NOTA CORRENTE - analizza principalmente il testo attuale
2. Le note precedenti sono SOLO contesto di supporto - NON descriverle una per una
3. Puoi fare riferimenti generici tipo "rispetto alle note precedenti", "in continuità con pattern emersi in precedenza"
4. Se menzioni una nota specifica precedente, cita SEMPRE la data completa (es: "come nella nota del 15/12/2025 alle ore 14:30")
5. NON elencare o riassumere ogni singola nota precedente
6. NON usare espressioni come "La nota 1", "La nota 2", "La nota 3" senza data e orario

COSA FARE:
✓ Analizzare gli aspetti emotivi, cognitivi e comportamentali della NOTA CORRENTE
✓ Notare eventuali cambiamenti o pattern rispetto al passato (in modo generico)
✓ Focalizzarsi su ciò che emerge OGGI nel testo

COSA NON FARE:
✗ NON descrivere in dettaglio le note precedenti
✗ NON fare un riassunto di ogni nota precedente
✗ NON citare numeri di note senza date
✗ NON usare markdown, elenchi puntati o simboli
✗ NON usare frasi introduttive come "Ecco la nota clinica", "Ecco l'analisi"

Completa sempre la frase, non troncare mai a metà. Inizia DIRETTAMENTE con il primo parametro.

Ora analizza questo testo (FOCALIZZATI SU QUESTO):
{testo}"""


def _genera_prompt_strutturato_lungo(testo, parametri_strutturati, tipo_parametri, max_chars, contesto_precedente):
    """Prompt per nota strutturata lunga"""
    return f"""Sei un assistente per uno psicoterapeuta. Analizza il seguente testo e fornisci una valutazione clinica strutturata e DETTAGLIATA.

CONTESTO - Note precedenti del paziente (SOLO per riferimento, NON descrivere ogni nota):
{contesto_precedente}

Esempio:
Testo: "Oggi ho fallito il mio esame e ho voglia di arrendermi."
Risposta:
{parametri_strutturati}

Parametri da utilizzare:
{tipo_parametri}

ISTRUZIONI FONDAMENTALI:
- La risposta deve essere DETTAGLIATA e APPROFONDITA (massimo {max_chars} caratteri)
- FORMATO OBBLIGATORIO: ogni parametro deve essere su una NUOVA RIGA nel formato "NomeParametro: valore"
- Vai a capo dopo ogni parametro
- Fornisci analisi complete per ogni parametro

REGOLE PER L'ANALISI:
1. CONCENTRATI AL 80% SULLA NOTA CORRENTE - analizza principalmente il testo attuale in profondità
2. Le note precedenti sono SOLO contesto di supporto - NON descriverle una per una
3. Puoi fare riferimenti come "Si nota un miglioramento rispetto al pattern ansioso emerso nelle settimane precedenti"
4. Se menzioni una nota specifica precedente, cita SEMPRE la data completa (es: "diversamente da quanto emerso nella nota del 15/12/2025 alle ore 14:30")
5. NON elencare o riassumere ogni singola nota precedente
6. NON usare espressioni come "Nella nota 1", "La nota 2 mostra", "Nella nota 3" senza data e orario
7. Puoi usare espressioni generiche come "nelle note precedenti", "in passato", "rispetto a situazioni simili"

COSA FARE:
✓ Analizzare in profondità la NOTA CORRENTE: emozioni, pensieri, comportamenti
✓ Identificare schemi cognitivi e pattern comportamentali visibili OGGI
✓ Notare progressi o regressioni rispetto al contesto generale passato
✓ Fornire osservazioni cliniche dettagliate sulla situazione ATTUALE

COSA NON FARE:
✗ NON dedicare paragrafi interi a descrivere le note precedenti
✗ NON fare un riassunto cronologico delle note passate
✗ NON citare numeri di note senza date complete
✗ NON usare markdown, elenchi puntati o simboli
✗ NON usare frasi introduttive come "Ecco la nota clinica"

Completa sempre la frase, non troncare mai a metà. Inizia DIRETTAMENTE con il primo parametro.

Ora analizza questo testo in profondità (QUESTO È IL FOCUS PRINCIPALE):
{testo}"""


def _genera_prompt_non_strutturato_breve(testo, max_chars, contesto_precedente):
    """Prompt per nota non strutturata breve"""
    return f"""Sei un assistente di uno psicoterapeuta specializzato. Analizza il seguente testo e fornisci una valutazione clinica discorsiva BREVE.

CONTESTO - Note precedenti del paziente (SOLO per riferimento, NON descrivere ogni nota):
{contesto_precedente}

ISTRUZIONI FONDAMENTALI:
- La risposta deve essere BREVE e SINTETICA (massimo {max_chars} caratteri)
- Scrivi in modo discorsivo, come un commento clinico professionale
- NON usare elenchi, grassetti, markdown, simboli o titoli

REGOLE PER L'ANALISI:
1. CONCENTRATI AL 90% SULLA NOTA CORRENTE - analizza principalmente il testo attuale
2. Le note precedenti sono SOLO contesto - menzionale brevemente se utile
3. Usa espressioni generiche come "rispetto alle note precedenti", "diversamente da prima"
4. Se menzioni una nota specifica, cita SEMPRE la data completa (es: "rispetto alla nota del 15/12/2025 alle ore 14:30")
5. NON dedicare frasi intere a riassumere le note precedenti
6. NON usare "La nota 1", "La nota 2", "Nella nota 3" senza date

COSA FARE:
✓ Analizzare il contenuto emotivo e psicologico della NOTA CORRENTE
✓ Identificare i vissuti emotivi emergenti OGGI
✓ Notare eventuali cambiamenti generali rispetto al passato
✓ Scrivere in modo fluido e professionale

COSA NON FARE:
✗ NON descrivere le note precedenti una per una
✗ NON fare un elenco delle emozioni passate
✗ NON citare numeri di note senza date
✗ NON usare frasi introduttive come "Ecco la nota clinica", "La valutazione è"

Inizia DIRETTAMENTE con l'analisi del contenuto emotivo/psicologico. Completa sempre la frase.

Testo da analizzare (QUESTO È IL FOCUS):
{testo}"""


def _genera_prompt_non_strutturato_lungo(testo, max_chars, contesto_precedente):
    """Prompt per nota non strutturata lunga"""
    return f"""Sei un assistente di uno psicoterapeuta specializzato. Analizza il seguente testo e fornisci una valutazione clinica discorsiva DETTAGLIATA e APPROFONDITA.

CONTESTO - Note precedenti del paziente (SOLO per riferimento, NON descrivere ogni nota):
{contesto_precedente}

ISTRUZIONI FONDAMENTALI:
- La risposta deve essere DETTAGLIATA e COMPLETA (massimo {max_chars} caratteri)
- Scrivi in modo discorsivo e professionale, come una nota clinica narrativa
- Approfondisci gli aspetti emotivi, cognitivi e comportamentali
- NON usare elenchi, grassetti, markdown, simboli o titoli

REGOLE PER L'ANALISI:
1. CONCENTRATI AL 80% SULLA NOTA CORRENTE - analizza in profondità il testo attuale
2. Le note precedenti sono SOLO contesto di supporto - NON descriverle una per una
3. Puoi fare riferimenti come "Si osserva un'evoluzione rispetto al pattern precedente", "Diversamente dalle situazioni passate"
4. Se menzioni una nota specifica, cita SEMPRE la data completa (es: "come emerso nella nota del 15/12/2025 alle ore 14:30")
5. NON dedicare paragrafi interi a riassumere le note precedenti
6. NON usare "La nota 1 descrive", "Nella nota 2", "La nota 3 rivela" senza date
7. Puoi usare espressioni generiche come "nelle note precedenti", "in passato", "rispetto a situazioni simili"

COSA FARE:
✓ Analizzare in profondità il contenuto emotivo della NOTA CORRENTE
✓ Esplorare i meccanismi cognitivi e i pattern comportamentali visibili OGGI
✓ Identificare i vissuti emotivi, le difese psicologiche, gli schemi ricorrenti nella situazione ATTUALE
✓ Contestualizzare in modo generico rispetto all'evoluzione del paziente
✓ Scrivere in modo fluido, professionale e clinicamente accurato

COSA NON FARE:
✗ NON fare un riassunto cronologico dettagliato delle note passate
✗ NON descrivere ogni singola nota precedente con paragrafi dedicati
✗ NON citare numeri di note senza date e orari completi
✗ NON usare espressioni come "Nella nota 1...", "La nota 2 mostra..." senza date
✗ NON usare frasi introduttive come "Ecco la nota clinica", "La valutazione è"

Inizia DIRETTAMENTE con l'analisi del contenuto emotivo/psicologico ATTUALE. Completa sempre la frase.

Testo da analizzare in profondità (QUESTO È IL FOCUS PRINCIPALE):
{testo}"""


def _recupera_contesto_note_precedenti(paziente, limite=5, escludi_nota_id=None):
    """
    Recupera le ultime note del paziente per fornire contesto, escludendo la nota corrente.

    Args:
        paziente: Oggetto Paziente
        limite: Numero massimo di note da recuperare (default 5)
        escludi_nota_id: ID della nota da escludere (tipicamente la nota corrente) (opzionale)

    Returns:
        String con il riepilogo delle note precedenti
    """
    # Filtra le note del paziente
    query = NotaDiario.objects.filter(paz=paziente)

    # Escludi la nota corrente se specificata
    if escludi_nota_id is not None:
        query = query.exclude(id=escludi_nota_id)

    # Prendi le ultime 'limite' note
    note_precedenti = query.order_by('-data_nota')[:limite]

    if not note_precedenti.exists():
        return "Nessuna nota precedente disponibile."

    contesto = []
    for i, nota in enumerate(reversed(list(note_precedenti)), 1):
        data_ora_formattata = nota.data_nota.strftime('%d/%m/%Y alle ore %H:%M')
        emozione = nota.emozione_predominante or "non specificata"
        testo_breve = nota.testo_paziente[:150] + "..." if len(nota.testo_paziente) > 150 else nota.testo_paziente
        contesto.append(f"Nota {i} (scritta il {data_ora_formattata}) - Emozione: {emozione}\nTesto: {testo_breve}")

    return "\n\n".join(contesto)


def genera_frasi_cliniche(testo, medico, paziente, nota_id=None):
    """
    Genera note cliniche personalizzate in base alle preferenze del medico.
    Include il contesto delle ultime 5 note del paziente (esclusa quella corrente) per una valutazione più completa.

    Args:
        testo: Testo della nota del paziente
        medico: Oggetto Medico
        paziente: Oggetto Paziente
        nota_id: ID della nota corrente da escludere dal contesto (opzionale)

    Gestisce 4 combinazioni:
    - Strutturata + Breve
    - Strutturata + Lunga
    - Non Strutturata + Breve
    - Non Strutturata + Lunga
    """
    print("Generazione commenti clinici con Ollama")

    try:
        tipo_nota = medico.tipo_nota  # True per "strutturato", False per "non strutturato"
        lunghezza_nota = medico.lunghezza_nota  # True per "lungo", False per "breve"
        tipo_parametri = medico.tipo_parametri.split(".:;!") if medico.tipo_parametri else []
        testo_parametri = medico.testo_parametri.split(".:;!") if medico.testo_parametri else []
        
        # Determina la lunghezza massima in caratteri
        max_chars = LUNGHEZZA_NOTA_LUNGA if lunghezza_nota else LUNGHEZZA_NOTA_BREVE

        # Recupera il contesto delle note precedenti (esclusa quella corrente)
        contesto_precedente = _recupera_contesto_note_precedenti(paziente, limite=5, escludi_nota_id=nota_id)

        if tipo_nota:
            # Nota strutturata
            parametri_strutturati = "\n".join(
                [f"{tipo}: {txt}" for tipo, txt in zip(tipo_parametri, testo_parametri)]
            )
            if lunghezza_nota:
                # Strutturata + Lunga
                prompt = _genera_prompt_strutturato_lungo(testo, parametri_strutturati, tipo_parametri, max_chars, contesto_precedente)
            else:
                # Strutturata + Breve
                prompt = _genera_prompt_strutturato_breve(testo, parametri_strutturati, tipo_parametri, max_chars, contesto_precedente)
        else:
            # Nota non strutturata
            if lunghezza_nota:
                # Non Strutturata + Lunga
                prompt = _genera_prompt_non_strutturato_lungo(testo, max_chars, contesto_precedente)
            else:
                # Non Strutturata + Breve
                prompt = _genera_prompt_non_strutturato_breve(testo, max_chars, contesto_precedente)

        return genera_con_ollama(prompt, max_chars=max_chars, temperature=0.6)

    except Exception as e:
        logger.error(f"Errore nella generazione clinica: {e}")
        return f"Errore durante la generazione: {e}"


def paziente_home(request):
    if request.session.get('user_type') != 'paziente':
        return redirect('/login/')

    paziente_id = request.session.get('user_id')
    if not paziente_id:
        return redirect('/login/')

    paziente = Paziente.objects.get(codice_fiscale=paziente_id)

    try:
        medico = paziente.med
    except Medico.DoesNotExist:
        medico = None
        print("Nessun medico trovato associato a questo paziente.")

    if request.method == 'POST':
        testo_paziente = request.POST.get('desc')
        generate_response_flag = request.POST.get('generateResponse') == 'on'
        testo_supporto = ""
        testo_clinico = ""
        emozione_predominante = ""
        spiegazione_emozione = ""

        if testo_paziente:
            if generate_response_flag:
                testo_supporto = genera_frasi_di_supporto(testo_paziente)

            testo_clinico = genera_frasi_cliniche(testo_paziente, medico, paziente)
            emozione_predominante, spiegazione_emozione = analizza_sentiment(testo_paziente)

            NotaDiario.objects.create(
                paz=paziente,
                testo_paziente=testo_paziente,
                testo_supporto=testo_supporto,
                testo_clinico=testo_clinico,
                emozione_predominante=emozione_predominante,
                spiegazione_emozione=spiegazione_emozione,
                data_nota=timezone.now()
            )

        # PRG Pattern: Redirect dopo POST per evitare duplicazione note al refresh
        return redirect('paziente_home')

    note_diario = NotaDiario.objects.filter(paz=paziente).order_by('-data_nota')

    return render(request, 'SoulDiaryConnectApp/paziente_home.html', {
        'paziente': paziente,
        'note_diario': note_diario,
        'medico': medico,
    })


def modifica_testo_medico(request, nota_id):
    if request.method == 'POST':
        nota = get_object_or_404(NotaDiario, id=nota_id)
        testo_medico = request.POST.get('testo_medico', '').strip()
        nota.testo_medico = testo_medico
        nota.save()
        return redirect(f'/medico/home/?paziente_id={nota.paz.codice_fiscale}')


def personalizza_generazione(request):
    if request.session.get('user_type') != 'medico':
        return redirect('/login/')

    medico_id = request.session.get('user_id')
    medico = Medico.objects.get(codice_identificativo=medico_id)

    if request.method == 'POST':
        # Tipo di Nota
        tipo_nota = request.POST.get('tipo_nota')
        medico.tipo_nota = True if tipo_nota == 'strutturato' else False

        # Lunghezza della Nota
        lunghezza_nota = request.POST.get('lunghezza_nota')
        medico.lunghezza_nota = True if lunghezza_nota == 'lungo' else False

        # Concatenazione di tipo_parametri e testo_parametri
        tipo_parametri = request.POST.getlist('tipo_parametri')
        testo_parametri = request.POST.getlist('testo_parametri')
        medico.tipo_parametri = ".:;!".join(tipo_parametri)
        medico.testo_parametri = ".:;!".join(testo_parametri)

        medico.save()
        return redirect('medico_home')

    # Suddivide i parametri già salvati in liste per visualizzarli nella tabella
    tipo_parametri = medico.tipo_parametri.split(".:;!") if medico.tipo_parametri else []
    testo_parametri = medico.testo_parametri.split(".:;!") if medico.testo_parametri else []

    return render(request, 'SoulDiaryConnectApp/personalizza_generazione.html', {
        'medico': medico,
        'tipo_parametri': zip(tipo_parametri, testo_parametri),
    })


def elimina_nota(request, nota_id):
    if request.session.get('user_type') != 'paziente':
        return redirect('/login/')
    nota = get_object_or_404(NotaDiario, id=nota_id)
    # Sicurezza: solo il proprietario può eliminare
    if nota.paz.codice_fiscale != request.session.get('user_id'):
        return redirect('/paziente/home/')
    if request.method == 'POST':
        nota.delete()
        return redirect('/paziente/home/')
    return render(request, 'SoulDiaryConnectApp/conferma_eliminazione.html', {'nota': nota})


def rigenera_frase_clinica(request):
    """
    View per rigenerare la frase clinica di una nota specifica (AJAX).
    """
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if request.method == 'POST' and is_ajax:
        nota_id = request.POST.get('nota_id')
        if not nota_id:
            return JsonResponse({'error': 'ID nota mancante.'}, status=400)
        try:
            nota = NotaDiario.objects.get(id=nota_id)
            medico = nota.paz.med
            paziente = nota.paz
            testo_paziente = nota.testo_paziente
            # Passa nota_id per escludere la nota corrente dal contesto
            nuova_frase = genera_frasi_cliniche(testo_paziente, medico, paziente, nota_id=nota.id)
            # Sostituisci la frase clinica precedente
            nota.testo_clinico = nuova_frase
            nota.save(update_fields=["testo_clinico"])
            return JsonResponse({'testo_clinico': nuova_frase})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Richiesta non valida.'}, status=400)


def genera_frase_supporto_nota(request, nota_id):
    """
    View per generare la frase di supporto per una nota specifica che non ce l'ha.
    """
    if request.session.get('user_type') != 'paziente':
        return redirect('/login/')

    nota = get_object_or_404(NotaDiario, id=nota_id)

    # Sicurezza: solo il proprietario può generare la frase di supporto
    if nota.paz.codice_fiscale != request.session.get('user_id'):
        return redirect('/paziente/home/')

    if request.method == 'POST':
        # Genera la frase di supporto se non esiste già
        if not nota.testo_supporto or nota.testo_supporto.strip() == '':
            testo_supporto = genera_frasi_di_supporto(nota.testo_paziente)
            nota.testo_supporto = testo_supporto
            nota.save(update_fields=["testo_supporto"])

        return redirect('/paziente/home/')

    return redirect('/paziente/home/')


def analisi_paziente(request):
    """
    Pagina dedicata alle analisi del paziente selezionato
    """
    if request.session.get('user_type') != 'medico':
        return redirect('/login/')

    medico_id = request.session.get('user_id')
    medico = get_object_or_404(Medico, codice_identificativo=medico_id)

    # Paziente selezionato
    paziente_id = request.GET.get('paziente_id')
    if not paziente_id:
        messages.error(request, 'Nessun paziente selezionato.')
        return redirect('medico_home')

    paziente_selezionato = get_object_or_404(Paziente, codice_fiscale=paziente_id)

    # Verifica che il paziente sia del medico loggato
    if paziente_selezionato.med != medico:
        messages.error(request, 'Non hai i permessi per visualizzare questo paziente.')
        return redirect('medico_home')

    # Note del paziente
    note_diario = NotaDiario.objects.filter(paz=paziente_selezionato).order_by('-data_nota')

    # Prepara i dati per il grafico delle emozioni
    emotion_chart_data = None
    statistiche = None

    if note_diario.exists():
        # Ordina le note per data (dalla più vecchia alla più recente per il grafico)
        note_ordinate = note_diario.order_by('data_nota')

        # Prepara le liste per il grafico
        dates = []
        emotions = []
        emotion_values = []

        # Mappa le categorie a valori numerici per il grafico
        # Usa le stesse categorie di EMOZIONI_CATEGORIE per coerenza
        category_score_map = {
            'positive': 4,   # Emozioni positive (verde)
            'neutral': 3,    # Emozioni neutre (lilla)
            'anxious': 2,    # Emozioni ansiose (giallo)
            'negative': 1,   # Emozioni negative (rosso)
        }

        # Contatore per le statistiche
        contatore_emozioni = {}
        somma_valori = 0

        for nota in note_ordinate:
            if nota.emozione_predominante:
                emozione_lower = nota.emozione_predominante.lower()
                dates.append(nota.data_nota.strftime('%d/%m/%Y'))
                emotions.append(emozione_lower)

                # Ottieni la categoria dell'emozione e il valore corrispondente
                categoria = get_emotion_category(emozione_lower)
                score = category_score_map.get(categoria, 2)  # default: neutral
                emotion_values.append(score)
                somma_valori += score

                # Conta le emozioni
                contatore_emozioni[emozione_lower] = contatore_emozioni.get(emozione_lower, 0) + 1

        if dates:
            emotion_chart_data = {
                'dates': json.dumps(dates),
                'emotions': json.dumps(emotions),
                'values': json.dumps(emotion_values),
            }

            # Calcola statistiche
            media_emotiva = somma_valori / len(emotion_values) if emotion_values else 0
            emozione_piu_frequente = max(contatore_emozioni.items(), key=lambda x: x[1]) if contatore_emozioni else (None, 0)

            statistiche = {
                'totale_note': note_diario.count(),
                'media_emotiva': round(media_emotiva, 2),
                'emozione_frequente': emozione_piu_frequente[0],
                'emozione_frequente_count': emozione_piu_frequente[1],
                'emozione_frequente_emoji': get_emoji_for_emotion(emozione_piu_frequente[0]),
            }

    return render(request, 'SoulDiaryConnectApp/analisi_paziente.html', {
        'medico': medico,
        'paziente': paziente_selezionato,
        'emotion_chart_data': emotion_chart_data,
        'statistiche': statistiche,
        'note_diario': note_diario,
    })


def riassunto_caso_clinico(request):
    """
    View per generare un riassunto del caso clinico di un paziente
    basato sulle note di un periodo selezionato.
    """
    if request.session.get('user_type') != 'medico':
        return redirect('/login/')

    medico_id = request.session.get('user_id')
    medico = get_object_or_404(Medico, codice_identificativo=medico_id)

    paziente_id = request.GET.get('paziente_id')
    periodo = request.GET.get('periodo', '7days')  # Default: ultimi 7 giorni

    if not paziente_id:
        messages.error(request, 'Seleziona un paziente.')
        return redirect('medico_home')

    paziente_selezionato = get_object_or_404(Paziente, codice_fiscale=paziente_id)

    # Verifica che il paziente sia del medico loggato
    if paziente_selezionato.med != medico:
        messages.error(request, 'Non hai i permessi per visualizzare questo paziente.')
        return redirect('medico_home')

    # Calcola la data di inizio in base al periodo selezionato
    from datetime import timedelta
    oggi = timezone.now()
    
    if periodo == '7days':
        data_inizio = oggi - timedelta(days=7)
        periodo_label = 'Ultimi 7 giorni'
    elif periodo == '30days':
        data_inizio = oggi - timedelta(days=30)
        periodo_label = 'Ultimo mese'
    elif periodo == '3months':
        data_inizio = oggi - timedelta(days=90)
        periodo_label = 'Ultimi 3 mesi'
    elif periodo == 'year':
        data_inizio = oggi - timedelta(days=365)
        periodo_label = 'Ultimo anno'
    else:
        data_inizio = oggi - timedelta(days=7)
        periodo_label = 'Ultimi 7 giorni'

    # Recupera le note del periodo selezionato
    note_periodo = NotaDiario.objects.filter(
        paz=paziente_selezionato,
        data_nota__gte=data_inizio
    ).order_by('data_nota')

    riassunto = None
    data_generazione = None
    
    # Controlla se è stata richiesta una nuova generazione
    if request.method == 'POST' or request.GET.get('genera') == '1':
        if note_periodo.exists():
            # Costruisci il contesto per il riassunto
            note_testo = []
            for nota in note_periodo:
                nota_info = f"Data: {nota.data_nota.strftime('%d/%m/%Y')}"
                if nota.emozione_predominante:
                    nota_info += f" | Emozione: {nota.emozione_predominante}"
                nota_info += f"\nNota paziente: {nota.testo_paziente}"
                if nota.testo_clinico:
                    nota_info += f"\nAnalisi clinica: {nota.testo_clinico}"
                note_testo.append(nota_info)
            
            contesto_note = "\n\n---\n\n".join(note_testo)
            
            prompt = f"""Sei uno psicologo clinico esperto. Il tuo compito è generare un riassunto clinico professionale dello stato del paziente basandoti sulle note del diario raccolte nel periodo specificato.

INFORMAZIONI PAZIENTE:
Nome: {paziente_selezionato.nome} {paziente_selezionato.cognome}
Periodo analizzato: {periodo_label}
Numero di note: {note_periodo.count()}

NOTE DEL DIARIO:
{contesto_note}

ISTRUZIONI:
1. Fornisci un riassunto clinico strutturato che includa:
   - Panoramica generale dello stato emotivo nel periodo
   - Pattern emotivi ricorrenti identificati
   - Eventuali miglioramenti o peggioramenti osservati
   - Aree di attenzione o preoccupazione
   - Raccomandazioni per il follow-up

2. Usa un linguaggio professionale e clinico
3. Sii obiettivo e basati solo sui dati forniti
4. Evidenzia eventuali trend significativi

Genera il riassunto clinico:"""

            riassunto = genera_con_ollama(prompt, max_chars=2000, temperature=0.5)
            data_generazione = timezone.now()
            
            # Salva o aggiorna il riassunto nel database
            riassunto_obj, created = RiassuntoCasoClinico.objects.update_or_create(
                paz=paziente_selezionato,
                med=medico,
                periodo=periodo,
                defaults={
                    'testo_riassunto': riassunto,
                    'data_generazione': data_generazione,
                }
            )
        else:
            riassunto = "Non sono presenti note nel periodo selezionato."
            data_generazione = timezone.now()
    else:
        # Cerca un riassunto esistente nel database
        riassunto_esistente = RiassuntoCasoClinico.objects.filter(
            paz=paziente_selezionato,
            med=medico,
            periodo=periodo
        ).first()
        
        if riassunto_esistente:
            riassunto = riassunto_esistente.testo_riassunto
            data_generazione = riassunto_esistente.data_generazione

    return render(request, 'SoulDiaryConnectApp/riassunto_caso_clinico.html', {
        'medico': medico,
        'paziente': paziente_selezionato,
        'periodo': periodo,
        'periodo_label': periodo_label,
        'note_periodo': note_periodo,
        'num_note': note_periodo.count(),
        'riassunto': riassunto,
        'data_generazione': data_generazione,
    })

