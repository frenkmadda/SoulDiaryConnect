from django.shortcuts import render, redirect, get_object_or_404
from .models import Medico, Paziente, NotaDiario, RiassuntoCasoClinico
from django.contrib import messages
from django.contrib.auth import logout
from django.utils import timezone
from django.http import JsonResponse
from django.core.cache import cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
import requests
import logging
import re
import json
import hashlib
import difflib
import threading

logger = logging.getLogger(__name__)

# Configurazione Ollama
OLLAMA_BASE_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1:8b"  # Cambia in "cbt-assistant" se hai creato il modello personalizzato

# Configurazione lunghezza note cliniche (in caratteri)
LUNGHEZZA_NOTA_BREVE = 250
LUNGHEZZA_NOTA_LUNGA = 500

# ============================================================================
# MODULO CRISI/EMERGENZA
# ============================================================================

# Parole chiave per rilevare contenuti di rischio
KEYWORDS_SUICIDIO = [
    'suicidio', 'suicidarmi', 'suicidarsi', 'uccidermi', 'uccidersi', 'farla finita',
    'togliermi la vita', 'non voglio più vivere', 'meglio morto', 'meglio morta',
    'voglio morire', 'vorrei morire', 'mi ammazzo', 'mi ammazzerei', 'pensieri di morte',
    'buttarmi', 'buttarmi giù', 'lanciarmi', 'impiccarmi', 'tagliarmi le vene',
    'overdose', 'prendere delle pastiglie', 'finirla', 'non ce la faccio più a vivere',
    'sarebbe meglio se non ci fossi', 'tutti starebbero meglio senza di me',
    'non valgo niente', 'non ho motivo di vivere', 'la vita non ha senso',
    'presto non sarò più un problema', 'ho deciso di farla finita',
    'ho un piano', 'ho pensato a come farlo', 'questa è la mia ultima',
]

KEYWORDS_VIOLENZA_STALKING = [
    'mi picchia', 'mi maltratta', 'mi perseguita', 'stalking', 'stalker',
    'mi segue', 'mi minaccia', 'minacce', 'violenza', 'abuso', 'abusato', 'abusata',
    'violentato', 'violentata', 'stupro', 'stuprata', 'stuprato', 'molestie',
    'molestato', 'molestata', 'aggredito', 'aggredita', 'botte', 'percosse',
    'picchiato', 'picchiata', 'mi fa del male', 'ho paura di lui', 'ho paura di lei',
    'mi controlla', 'controllo ossessivo', 'non mi lascia uscire', 'mi isola',
    'mi terrorizza', 'relazione tossica', 'violenza domestica', 'maltrattamenti', 'picchiarmi',
    'maltrattarmi', 'perseguitarmi', 'minacciarmi', 'aggredirmi', 'molestarmi', 'abuso sessuale',
    'picchiato', 'picchiata', 'maltrattato', 'maltrattata', 'perseguitato', 'perseguitata',
]

KEYWORDS_AUTOLESIONISMO = [
    'mi taglio', 'mi faccio del male', 'autolesionismo', 'ferirmi', 'farmi male',
    'bruciarmi', 'graffiarmi', 'punirmi fisicamente', 'mi colpisco', 'mi faccio tagli',
]

# Numeri di emergenza
NUMERI_EMERGENZA = {
    'suicidio': {
        'principale': {
            'nome': 'Telefono Azzurro',
            'numero': '19696',
            'orari': '24 ore su 24, 7 giorni su 7'
        },
        'alternativo': {
            'nome': 'Telefono Amico Italia',
            'numero': '02 2327 2327',
            'orari': 'tutti i giorni dalle 9:00 alle 24:00'
        }
    },
    'violenza': {
        'principale': {
            'nome': 'Numero Antiviolenza e Stalking',
            'numero': '1522',
            'orari': '24 ore su 24, 7 giorni su 7 (gratuito)'
        }
    },
    'autolesionismo': {
        'principale': {
            'nome': 'Telefono Azzurro',
            'numero': '19696',
            'orari': '24 ore su 24, 7 giorni su 7'
        },
        'alternativo': {
            'nome': 'Telefono Amico Italia',
            'numero': '02 2327 2327',
            'orari': 'tutti i giorni dalle 9:00 alle 24:00'
        }
    }
}

# Messaggi di conforto per tipo di emergenza
MESSAGGI_CONFORTO = {
    'suicidio': """Capisco che stai attraversando un momento di grande sofferenza. Quello che provi è reale e importante, e non devi affrontarlo da solo/a. 

In questo momento è fondamentale che tu possa parlare con qualcuno che può aiutarti. Ti prego, contatta subito il tuo medico {nome_medico} al numero {telefono_medico}, oppure:

📞 <strong>Telefono Azzurro: 19696</strong> (attivo 24/7)
📞 <strong>Telefono Amico Italia: 02 2327 2327</strong> (attivo tutti i giorni dalle 9:00 alle 24:00)

Non sei solo/a. Ci sono persone pronte ad ascoltarti e ad aiutarti in questo momento difficile. La tua vita ha valore.""",

    'violenza': """Mi preoccupo per la tua sicurezza. Quello che stai vivendo non è giusto e non devi affrontarlo da solo/a.

È importante che tu possa ricevere supporto e protezione. Contatta subito il tuo medico {nome_medico} al numero {telefono_medico}, oppure:

📞 <strong>Numero Antiviolenza e Stalking: 1522</strong> (gratuito, attivo 24/7)

Il 1522 offre supporto professionale, anonimo e gratuito. Possono aiutarti a trovare una via d'uscita sicura. Non sei solo/a e meriti di vivere senza paura.""",

    'autolesionismo': """Capisco che stai soffrendo molto e che forse senti il bisogno di sfogare il dolore. Ma ci sono modi più sicuri per gestire queste emozioni intense.

Ti prego, parla con qualcuno che può aiutarti. Contatta il tuo medico {nome_medico} al numero {telefono_medico}, oppure:

📞 <strong>Telefono Azzurro: 19696</strong> (attivo 24/7)
📞 <strong>Telefono Amico Italia: 02 2327 2327</strong> (attivo tutti i giorni dalle 9:00 alle 24:00)

Non devi affrontare questo da solo/a. Ci sono persone pronte ad ascoltarti senza giudicarti."""
}


def rileva_contenuto_crisi(testo):
    """
    Analizza il testo per rilevare contenuti di rischio/crisi.

    Args:
        testo: Il testo della nota del paziente

    Returns:
        tuple: (is_emergency, tipo_emergenza)
               is_emergency: True se rilevato contenuto di rischio
               tipo_emergenza: 'suicidio', 'violenza', 'autolesionismo', o 'none'
    """
    if not testo:
        return False, 'none'

    testo_lower = testo.lower()

    # Controlla prima il suicidio (priorità più alta)
    for keyword in KEYWORDS_SUICIDIO:
        if keyword in testo_lower:
            logger.warning(f"EMERGENZA RILEVATA - Tipo: suicidio - Keyword: {keyword}")
            return True, 'suicidio'

    # Controlla violenza/stalking
    for keyword in KEYWORDS_VIOLENZA_STALKING:
        if keyword in testo_lower:
            logger.warning(f"EMERGENZA RILEVATA - Tipo: violenza - Keyword: {keyword}")
            return True, 'violenza'

    # Controlla autolesionismo
    for keyword in KEYWORDS_AUTOLESIONISMO:
        if keyword in testo_lower:
            logger.warning(f"EMERGENZA RILEVATA - Tipo: autolesionismo - Keyword: {keyword}")
            return True, 'autolesionismo'

    return False, 'none'


def genera_messaggio_emergenza(tipo_emergenza, medico):
    """
    Genera il messaggio di emergenza personalizzato con i contatti del medico.

    Args:
        tipo_emergenza: Il tipo di emergenza rilevata
        medico: L'oggetto Medico del paziente

    Returns:
        str: Il messaggio di emergenza formattato
    """
    if tipo_emergenza not in MESSAGGI_CONFORTO:
        return None

    # Prepara i dati del medico
    nome_medico = f"Dr. {medico.nome} {medico.cognome}" if medico else "il tuo medico"

    # Preferisci il cellulare, altrimenti il telefono dello studio
    if medico and medico.numero_telefono_cellulare:
        telefono_medico = medico.numero_telefono_cellulare
    elif medico and medico.numero_telefono_studio:
        telefono_medico = medico.numero_telefono_studio
    else:
        telefono_medico = "(contattalo via email)"

    messaggio = MESSAGGI_CONFORTO[tipo_emergenza].format(
        nome_medico=nome_medico,
        telefono_medico=telefono_medico
    )

    return messaggio


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
            nota.context_emoji = get_emoji_for_context(nota.contesto_sociale)

    return render(request, 'SoulDiaryConnectApp/medico_home.html', {
        'medico': medico,
        'pazienti': pazienti,
        'paziente_selezionato': paziente_selezionato,
        'note_diario': note_diario,
    })


def genera_frasi_di_supporto(testo, paziente=None):
    """
    Genera frasi di supporto empatico per il paziente usando Ollama

    Args:
        testo: Il testo della nota del paziente
        paziente: L'oggetto Paziente (opzionale, per evitare confusione con altri nomi nel testo)
    """
    print("Generazione frasi supporto con Ollama")

    # Costruisco il contesto sul paziente se disponibile
    contesto_paziente = ""
    if paziente:
        nome_completo = f"{paziente.nome} {paziente.cognome}"
        contesto_paziente = f"""INFORMAZIONE IMPORTANTE SULL'AUTORE:
L'autore di questo testo è {nome_completo}.
Questo testo è scritto in prima persona da {nome_completo}.
Qualsiasi altro nome menzionato (anche se uguale a "{paziente.nome}") si riferisce ad altre persone (amici, familiari, colleghi, ecc.), NON all'autore.
Quando rispondi, rivolgiti direttamente a {paziente.nome} (o usa "tu" senza nominarlo).

"""

    prompt = f"""Sei un assistente empatico e di supporto emotivo. Il tuo compito è rispondere con calore e comprensione a persone che stanno attraversando momenti difficili.

{contesto_paziente}Esempio:
Testo del paziente: "Ho fallito il mio esame e ho voglia di arrendermi."
Risposta di supporto: "Mi dispiace molto per il tuo esame. È normale sentirsi delusi, ma questo non definisce il tuo valore come persona. Potresti provare a rivedere il tuo metodo di studio e chiedere aiuto se ne hai bisogno. Ce la puoi fare!"

ISTRUZIONI:
- Rispondi in italiano con tono caldo, empatico e incoraggiante
- Riconosci e valida le emozioni espresse
- Offri una prospettiva positiva senza minimizzare i sentimenti
- Suggerisci delicatamente possibili strategie o riflessioni utili
- Non usare un tono clinico o distaccato
- Completa sempre la risposta, non troncare mai a metà
- NON confondere l'autore del testo con altre persone menzionate nella nota

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

# Dizionario dei contesti sociali con le relative emoji
CONTESTI_EMOJI = {
    'lavoro': '💼',
    'università': '🎓',
    'scuola': '📚',
    'famiglia': '👨‍👩‍👧‍👦',
    'amicizia': '👥',
    'relazione': '💑',
    'salute': '🏥',
    'sport': '🏋️',
    'palestra': '💪',
    'tempo libero': '🎮',
    'hobby': '🎨',
    'viaggi': '✈️',
    'casa': '🏠',
    'finanze': '💰',
    'spiritualità': '🧘',
    'sociale': '🌐',
    'solitudine': '🚶',
    'studio': '📖',
    'alimentazione': '🍽️',
    'sonno': '😴',
    'altro': '📝',
}


def get_emotion_category(emozione):
    """
    Restituisce la categoria dell'emozione per la colorazione CSS.
    """
    if not emozione:
        return 'neutral'
    emozione_lower = emozione.lower().strip()
    return EMOZIONI_CATEGORIE.get(emozione_lower, 'neutral')


def get_emoji_for_context(contesto):
    """
    Restituisce l'emoji corrispondente al contesto sociale.
    Se il contesto non è nel dizionario, restituisce un'emoji di default.
    """
    if not contesto:
        return '📝'
    contesto_lower = contesto.lower().strip()
    return CONTESTI_EMOJI.get(contesto_lower, '📝')


def analizza_contesto_sociale(testo, paziente=None):
    """
    Analizza il contesto sociale del testo del paziente e restituisce il contesto principale
    con relativa spiegazione.

    Args:
        testo: Il testo della nota del paziente
        paziente: L'oggetto Paziente (opzionale, per evitare confusione con altri nomi nel testo)

    Returns:
        tuple: (contesto, spiegazione)
    """
    print("Analisi contesto sociale con Ollama")

    contesti_lista = ', '.join(CONTESTI_EMOJI.keys())

    # Costruisco il contesto sul paziente se disponibile
    info_paziente = ""
    if paziente:
        nome_completo = f"{paziente.nome} {paziente.cognome}"
        info_paziente = f"""INFORMAZIONE IMPORTANTE SULL'AUTORE:
L'autore di questo testo è {nome_completo}.
Questo testo è scritto in prima persona da {nome_completo}.
Qualsiasi altro nome menzionato (anche se uguale a "{paziente.nome}") si riferisce ad altre persone (amici, familiari, colleghi, ecc.), NON all'autore.
Identifica il contesto sociale in cui si trova {nome_completo}, l'autore del testo.

"""

    prompt = f"""Sei un esperto di analisi del contesto sociale. Il tuo compito è identificare il contesto sociale principale in cui si svolge il racconto di un paziente e spiegare perché.

{info_paziente}CONTESTI DISPONIBILI (scegli SOLO tra questi):
{contesti_lista}

FORMATO RISPOSTA (OBBLIGATORIO):
Contesto: [una sola parola o due parole dalla lista]
Spiegazione: [breve spiegazione di 1-2 frasi che cita elementi specifici del testo]

REGOLE FONDAMENTALI:
1. La prima riga DEVE iniziare con "Contesto:" seguita da UNA o DUE PAROLE dalla lista
2. La seconda riga DEVE iniziare con "Spiegazione:" seguita dalla motivazione
3. Nella spiegazione, cita parole o frasi SPECIFICHE del testo originale
4. La spiegazione deve essere breve (max 2 frasi)
5. NON inventare contesti non presenti nella lista
6. Se il testo non indica chiaramente un contesto, usa "altro"

REGOLE SPECIFICHE IMPORTANTI:
- L'attività fisica (palestra, allenamento, corsa, nuoto, calcio, fitness, yoga, esercizi, pesi, cardio, crossfit, ecc.) va SEMPRE classificata come "palestra" o "sport", MAI come "tempo libero"
- "tempo libero" si usa solo per attività ricreative NON sportive come: videogiochi, TV, cinema, lettura, uscite con amici per svago, shopping, ecc.

COME DISTINGUERE I CONTESTI RELAZIONALI (MOLTO IMPORTANTE):
- "famiglia": usa SOLO se il testo menziona ESPLICITAMENTE familiari (madre, padre, fratello, sorella, figlio, figlia, marito, moglie, nonno, nonna, zio, zia, cugino, ecc.)
- "relazione": usa quando il testo parla di partner sentimentale/romantico (fidanzato/a, compagno/a, relazione amorosa, baci, intimità, sentimenti romantici, paura di investire in una relazione, gelosia sentimentale)
- "amicizia": usa per amici, compagni, conoscenti (senza connotazione romantica)
- Se una persona viene descritta con dinamiche romantiche/sentimentali (es. "investire su qualcuno", "gelosia", "amore", gesti affettuosi romantici) = "relazione"
- NON assumere che qualcuno sia un familiare solo perché è una persona cara

ESEMPI CORRETTI:
Testo: "Oggi al lavoro il mio capo mi ha criticato davanti a tutti i colleghi"
Contesto: lavoro
Spiegazione: Il testo si svolge chiaramente in ambito lavorativo, con riferimenti espliciti al "lavoro", al "capo" e ai "colleghi".

Testo: "Ho litigato con mia madre perché non capisce le mie scelte"
Contesto: famiglia
Spiegazione: Il testo descrive una dinamica familiare, con riferimento esplicito a "mia madre" e a un conflitto intergenerazionale.

Testo: "Ho passato la serata con Marco e abbiamo giocato alla PlayStation"
Contesto: amicizia
Spiegazione: Il testo descrive un momento di svago con un amico, senza connotazioni romantiche o familiari.

Testo: "Ieri sera io e Laura ci siamo baciati per la prima volta, il mio cuore batteva fortissimo"
Contesto: relazione
Spiegazione: Il testo descrive chiaramente un momento romantico e sentimentale con "bacio" e riferimenti a sentimenti d'amore.

Testo: "Sono andato in palestra e mi sono allenato duramente"
Contesto: palestra
Spiegazione: Il testo menziona esplicitamente la "palestra" e l'allenamento fisico.

Testo: "Oggi ho fatto una bella corsa al parco e poi esercizi a casa"
Contesto: sport
Spiegazione: Il testo descrive attività fisica come "corsa" ed "esercizi", che rientrano nel contesto sportivo.

Testo da analizzare:
{testo}

Rispondi ora nel formato richiesto:"""

    risposta = genera_con_ollama(prompt, max_chars=400, temperature=0.2)

    print(f"Risposta contesto sociale raw: {risposta}")

    # Parsing della risposta
    linee = risposta.strip().split('\n')
    contesto = None
    spiegazione = None

    for linea in linee:
        linea_stripped = linea.strip()
        if linea_stripped.lower().startswith('contesto:'):
            contesto = linea_stripped.split(':', 1)[1].strip().lower().rstrip('.!?,;:')
        elif linea_stripped.lower().startswith('spiegazione:'):
            spiegazione = linea_stripped.split(':', 1)[1].strip()

    print(f"Contesto parsed: {contesto}, Spiegazione parsed: {spiegazione}")

    # Validazione e normalizzazione del contesto
    if contesto and contesto in CONTESTI_EMOJI:
        contesto_validato = contesto
    else:
        # Fallback con fuzzy matching
        contesto_validato = 'altro'
        for chiave in CONTESTI_EMOJI.keys():
            if contesto and chiave in contesto:
                contesto_validato = chiave
                break

        # Controllo sinonimi
        sinonimi = {
            'ufficio': 'lavoro',
            'azienda': 'lavoro',
            'professione': 'lavoro',
            'carriera': 'lavoro',
            'college': 'università',
            'ateneo': 'università',
            'liceo': 'scuola',
            'elementare': 'scuola',
            'media': 'scuola',
            'genitori': 'famiglia',
            'fratelli': 'famiglia',
            'parenti': 'famiglia',
            'figli': 'famiglia',
            'madre': 'famiglia',
            'padre': 'famiglia',
            'mamma': 'famiglia',
            'papà': 'famiglia',
            'sorella': 'famiglia',
            'fratello': 'famiglia',
            'amici': 'amicizia',
            'compagni': 'amicizia',
            'amico': 'amicizia',
            'amica': 'amicizia',
            'partner': 'relazione',
            'fidanzato': 'relazione',
            'fidanzata': 'relazione',
            'marito': 'relazione',
            'moglie': 'relazione',
            'compagno': 'relazione',
            'compagna': 'relazione',
            'ragazzo': 'relazione',
            'ragazza': 'relazione',
            'sentimentale': 'relazione',
            'romantico': 'relazione',
            'romantica': 'relazione',
            'coppia': 'relazione',
            'amore': 'relazione',
            'innamorato': 'relazione',
            'innamorata': 'relazione',
            'medico': 'salute',
            'ospedale': 'salute',
            'malattia': 'salute',
            'allenamento': 'palestra',
            'allenarsi': 'palestra',
            'corsa': 'sport',
            'correre': 'sport',
            'nuoto': 'sport',
            'nuotare': 'sport',
            'calcio': 'sport',
            'tennis': 'sport',
            'basket': 'sport',
            'pallavolo': 'sport',
            'ciclismo': 'sport',
            'bicicletta': 'sport',
            'fitness': 'palestra',
            'pesi': 'palestra',
            'cardio': 'palestra',
            'crossfit': 'palestra',
            'yoga': 'palestra',
            'pilates': 'palestra',
            'esercizi': 'palestra',
            'esercizio': 'palestra',
            'attività fisica': 'sport',
            'ginnastica': 'palestra',
            'svago': 'tempo libero',
            'divertimento': 'tempo libero',
            'passatempo': 'hobby',
            'vacanza': 'viaggi',
            'viaggio': 'viaggi',
            'appartamento': 'casa',
            'soldi': 'finanze',
            'economia': 'finanze',
            'meditazione': 'spiritualità',
            'religione': 'spiritualità',
            'esame': 'studio',
            'compiti': 'studio',
            'cibo': 'alimentazione',
            'dieta': 'alimentazione',
            'dormire': 'sonno',
            'insonnia': 'sonno',
        }

        if contesto and contesto in sinonimi:
            contesto_validato = sinonimi[contesto]

    if not spiegazione:
        spiegazione = "Contesto rilevato in base al contenuto generale del testo."

    print(f"Contesto rilevato: {contesto_validato}, Spiegazione: {spiegazione}")

    return contesto_validato, spiegazione


def analizza_sentiment(testo, paziente=None):
    """
    Analizza il sentiment del testo del paziente e restituisce l'emozione predominante
    con relativa spiegazione.

    Args:
        testo: Il testo della nota del paziente
        paziente: L'oggetto Paziente (opzionale, per evitare confusione con altri nomi nel testo)

    Returns:
        tuple: (emozione, spiegazione)
    """
    print("Analisi sentiment con Ollama")

    emozioni_lista = ', '.join(EMOZIONI_EMOJI.keys())
    
    # Costruisco il contesto sul paziente se disponibile
    info_paziente = ""
    if paziente:
        nome_completo = f"{paziente.nome} {paziente.cognome}"
        info_paziente = f"""INFORMAZIONE IMPORTANTE SULL'AUTORE:
L'autore di questo testo è {nome_completo}.
Questo testo è scritto in prima persona da {nome_completo}.
Qualsiasi altro nome menzionato (anche se uguale a "{paziente.nome}") si riferisce ad altre persone (amici, familiari, colleghi, ecc.), NON all'autore.
Analizza le emozioni di {nome_completo}, l'autore del testo.

"""

    prompt = f"""Sei un esperto di analisi delle emozioni. Il tuo compito è identificare l'emozione predominante in un testo e spiegare perché.

{info_paziente}EMOZIONI DISPONIBILI (scegli SOLO tra queste):
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


def _genera_prompt_strutturato_breve(testo, parametri_strutturati, tipo_parametri, max_chars, contesto_precedente, paziente=None):
    """Prompt per nota strutturata breve"""
    info_paziente = ""
    if paziente:
        nome_completo = f"{paziente.nome} {paziente.cognome}"
        info_paziente = f"""INFORMAZIONE IMPORTANTE SULL'AUTORE:
L'autore di questo testo è {nome_completo}.
Questo testo è scritto in prima persona da {nome_completo}.
Qualsiasi altro nome menzionato (anche se uguale a "{paziente.nome}") si riferisce ad altre persone (amici, familiari, colleghi, ecc.), NON al paziente.

"""
    return f"""Sei un assistente per uno psicoterapeuta. Analizza il seguente testo e fornisci una valutazione clinica strutturata e CONCISA.

{info_paziente}CONTESTO - Note precedenti del paziente (SOLO per riferimento, NON descrivere ogni nota):
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


def _genera_prompt_strutturato_lungo(testo, parametri_strutturati, tipo_parametri, max_chars, contesto_precedente, paziente=None):
    """Prompt per nota strutturata lunga"""
    info_paziente = ""
    if paziente:
        nome_completo = f"{paziente.nome} {paziente.cognome}"
        info_paziente = f"""INFORMAZIONE IMPORTANTE SULL'AUTORE:
L'autore di questo testo è {nome_completo}.
Questo testo è scritto in prima persona da {nome_completo}.
Qualsiasi altro nome menzionato (anche se uguale a "{paziente.nome}") si riferisce ad altre persone (amici, familiari, colleghi, ecc.), NON al paziente.

"""
    return f"""Sei un assistente per uno psicoterapeuta. Analizza il seguente testo e fornisci una valutazione clinica strutturata e DETTAGLIATA.

{info_paziente}CONTESTO - Note precedenti del paziente (SOLO per riferimento, NON descrivere ogni nota):
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


def _genera_prompt_non_strutturato_breve(testo, max_chars, contesto_precedente, paziente=None):
    """Prompt per nota non strutturata breve"""
    info_paziente = ""
    if paziente:
        nome_completo = f"{paziente.nome} {paziente.cognome}"
        info_paziente = f"""INFORMAZIONE IMPORTANTE SULL'AUTORE:
L'autore di questo testo è {nome_completo}.
Questo testo è scritto in prima persona da {nome_completo}.
Qualsiasi altro nome menzionato (anche se uguale a "{paziente.nome}") si riferisce ad altre persone (amici, familiari, colleghi, ecc.), NON al paziente.

"""
    return f"""Sei un assistente di uno psicoterapeuta specializzato. Analizza il seguente testo e fornisci una valutazione clinica discorsiva BREVE.

{info_paziente}CONTESTO - Note precedenti del paziente (SOLO per riferimento, NON descrivere ogni nota):
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


def _genera_prompt_non_strutturato_lungo(testo, max_chars, contesto_precedente, paziente=None):
    """Prompt per nota non strutturata lunga"""
    info_paziente = ""
    if paziente:
        nome_completo = f"{paziente.nome} {paziente.cognome}"
        info_paziente = f"""INFORMAZIONE IMPORTANTE SULL'AUTORE:
L'autore di questo testo è {nome_completo}.
Questo testo è scritto in prima persona da {nome_completo}.
Qualsiasi altro nome menzionato (anche se uguale a "{paziente.nome}") si riferisce ad altre persone (amici, familiari, colleghi, ecc.), NON al paziente.

"""
    return f"""Sei un assistente di uno psicoterapeuta specializzato. Analizza il seguente testo e fornisci una valutazione clinica discorsiva DETTAGLIATA e APPROFONDITA.

{info_paziente}CONTESTO - Note precedenti del paziente (SOLO per riferimento, NON descrivere ogni nota):
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
                prompt = _genera_prompt_strutturato_lungo(testo, parametri_strutturati, tipo_parametri, max_chars, contesto_precedente, paziente)
            else:
                # Strutturata + Breve
                prompt = _genera_prompt_strutturato_breve(testo, parametri_strutturati, tipo_parametri, max_chars, contesto_precedente, paziente)
        else:
            # Nota non strutturata
            if lunghezza_nota:
                # Non Strutturata + Lunga
                prompt = _genera_prompt_non_strutturato_lungo(testo, max_chars, contesto_precedente, paziente)
            else:
                # Non Strutturata + Breve
                prompt = _genera_prompt_non_strutturato_breve(testo, max_chars, contesto_precedente, paziente)

        return genera_con_ollama(prompt, max_chars=max_chars, temperature=0.6)

    except Exception as e:
        logger.error(f"Errore nella generazione clinica: {e}")
        return f"Errore durante la generazione: {e}"


def genera_analisi_in_background(nota_id, testo_paziente, medico, paziente):
    """
    Funzione che viene eseguita in un thread separato per generare
    l'analisi clinica, sentiment e contesto sociale in background.
    """
    from django.db import connection
    try:
        # Genera le analisi
        testo_clinico = genera_frasi_cliniche(testo_paziente, medico, paziente)
        emozione_predominante, spiegazione_emozione = analizza_sentiment(testo_paziente, paziente)
        contesto_sociale, spiegazione_contesto = analizza_contesto_sociale(testo_paziente, paziente)
        
        # Aggiorna la nota nel database
        nota = NotaDiario.objects.get(id=nota_id)
        nota.testo_clinico = testo_clinico
        nota.emozione_predominante = emozione_predominante
        nota.spiegazione_emozione = spiegazione_emozione
        nota.contesto_sociale = contesto_sociale
        nota.spiegazione_contesto = spiegazione_contesto
        nota.generazione_in_corso = False
        nota.save()
        
        logger.info(f"Generazione in background completata per nota {nota_id}")
    except Exception as e:
        logger.error(f"Errore nella generazione in background per nota {nota_id}: {e}")
        # Imposta comunque generazione_in_corso a False per evitare blocchi
        try:
            nota = NotaDiario.objects.get(id=nota_id)
            nota.generazione_in_corso = False
            nota.testo_clinico = "Errore durante la generazione dell'analisi clinica."
            nota.save()
        except:
            pass
    finally:
        connection.close()


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
        is_emergency = False
        tipo_emergenza = 'none'
        messaggio_emergenza = None

        if testo_paziente:
            # PRIMA: Controlla se c'è un contenuto di crisi/emergenza
            is_emergency, tipo_emergenza = rileva_contenuto_crisi(testo_paziente)

            if is_emergency:
                # Se è una situazione di emergenza, genera il messaggio di sicurezza
                # e NON genera il supporto automatico dell'LLM
                messaggio_emergenza = genera_messaggio_emergenza(tipo_emergenza, medico)
                testo_supporto = ""  # Non generare supporto LLM in emergenza
                logger.warning(f"EMERGENZA RILEVATA per paziente {paziente.codice_fiscale} - Tipo: {tipo_emergenza}")
            else:
                # Situazione normale: genera supporto se richiesto
                if generate_response_flag:
                    testo_supporto = genera_frasi_di_supporto(testo_paziente, paziente)

            # Crea la nota immediatamente con il supporto generato
            # L'analisi clinica e sentiment verranno generati in background
            nota = NotaDiario.objects.create(
                paz=paziente,
                testo_paziente=testo_paziente,
                testo_supporto=testo_supporto,
                testo_clinico="",  # Sarà generato in background
                emozione_predominante="",
                spiegazione_emozione="",
                contesto_sociale="",
                spiegazione_contesto="",
                data_nota=timezone.now(),
                is_emergency=is_emergency,
                tipo_emergenza=tipo_emergenza,
                messaggio_emergenza=messaggio_emergenza,
                generazione_in_corso=True  # Flag per indicare che la generazione è in corso
            )
            
            # Avvia la generazione dell'analisi clinica in background
            thread = threading.Thread(
                target=genera_analisi_in_background,
                args=(nota.id, testo_paziente, medico, paziente)
            )
            thread.daemon = True
            thread.start()

        # PRG Pattern: Redirect dopo POST per evitare duplicazione note al refresh
        return redirect('paziente_home')

    note_diario = NotaDiario.objects.filter(paz=paziente).order_by('-data_nota')

    return render(request, 'SoulDiaryConnectApp/paziente_home.html', {
        'paziente': paziente,
        'note_diario': note_diario,
        'medico': medico,
    })


def controlla_stato_generazione(request, nota_id):
    """
    View AJAX per controllare lo stato di generazione di una nota.
    Usata dal lato medico per aggiornare la UI quando la generazione è completata.
    """
    try:
        nota = NotaDiario.objects.get(id=nota_id)
        return JsonResponse({
            'generazione_in_corso': nota.generazione_in_corso,
            'testo_clinico': nota.testo_clinico if not nota.generazione_in_corso else None,
            'emozione_predominante': nota.emozione_predominante if not nota.generazione_in_corso else None,
            'spiegazione_emozione': nota.spiegazione_emozione if not nota.generazione_in_corso else None,
            'contesto_sociale': nota.contesto_sociale if not nota.generazione_in_corso else None,
            'spiegazione_contesto': nota.spiegazione_contesto if not nota.generazione_in_corso else None,
        })
    except NotaDiario.DoesNotExist:
        return JsonResponse({'error': 'Nota non trovata'}, status=404)


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
            testo_supporto = genera_frasi_di_supporto(nota.testo_paziente, nota.paz)
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

    # Prepara i dati per le correlazioni umore-contesto sociale
    correlazione_contesto_data = None
    if note_diario.exists():
        # Raccogli dati per correlazione
        contesto_emozioni = {}  # {contesto: {'positive': n, 'neutral': n, 'anxious': n, 'negative': n, 'total': n, 'sum': n}}

        for nota in note_diario:
            contesto = nota.contesto_sociale
            emozione = nota.emozione_predominante

            if contesto and emozione:
                contesto_lower = contesto.lower().strip()
                emozione_lower = emozione.lower().strip()
                categoria_emozione = get_emotion_category(emozione_lower)

                if contesto_lower not in contesto_emozioni:
                    contesto_emozioni[contesto_lower] = {
                        'positive': 0,
                        'neutral': 0,
                        'anxious': 0,
                        'negative': 0,
                        'total': 0,
                        'sum': 0,
                        'emoji': get_emoji_for_context(contesto_lower),
                    }

                contesto_emozioni[contesto_lower][categoria_emozione] += 1
                contesto_emozioni[contesto_lower]['total'] += 1
                # Calcola il valore numerico per la media
                category_score_map = {'positive': 4, 'neutral': 3, 'anxious': 2, 'negative': 1}
                contesto_emozioni[contesto_lower]['sum'] += category_score_map.get(categoria_emozione, 2)

        if contesto_emozioni:
            # Ordina per numero totale di occorrenze (decrescente)
            contesti_ordinati = sorted(contesto_emozioni.items(), key=lambda x: x[1]['total'], reverse=True)

            # Prepara i dati per il grafico a barre raggruppate
            labels = []
            positive_data = []
            neutral_data = []
            anxious_data = []
            negative_data = []
            medie_contesto = []
            emojis = []

            for contesto, dati in contesti_ordinati:
                labels.append(contesto.title())
                positive_data.append(dati['positive'])
                neutral_data.append(dati['neutral'])
                anxious_data.append(dati['anxious'])
                negative_data.append(dati['negative'])
                media = round(dati['sum'] / dati['total'], 2) if dati['total'] > 0 else 0
                medie_contesto.append(media)
                emojis.append(dati['emoji'])

            # Trova contesto più positivo e più negativo
            contesto_migliore = max(contesti_ordinati, key=lambda x: x[1]['sum'] / x[1]['total'] if x[1]['total'] > 0 else 0)
            contesto_peggiore = min(contesti_ordinati, key=lambda x: x[1]['sum'] / x[1]['total'] if x[1]['total'] > 0 else 0)

            correlazione_contesto_data = {
                'labels': json.dumps(labels),
                'positive': json.dumps(positive_data),
                'neutral': json.dumps(neutral_data),
                'anxious': json.dumps(anxious_data),
                'negative': json.dumps(negative_data),
                'medie': json.dumps(medie_contesto),
                'emojis': json.dumps(emojis),
                'contesto_migliore': contesto_migliore[0].title(),
                'contesto_migliore_emoji': contesto_migliore[1]['emoji'],
                'contesto_migliore_media': round(contesto_migliore[1]['sum'] / contesto_migliore[1]['total'], 2) if contesto_migliore[1]['total'] > 0 else 0,
                'contesto_peggiore': contesto_peggiore[0].title(),
                'contesto_peggiore_emoji': contesto_peggiore[1]['emoji'],
                'contesto_peggiore_media': round(contesto_peggiore[1]['sum'] / contesto_peggiore[1]['total'], 2) if contesto_peggiore[1]['total'] > 0 else 0,
            }

    return render(request, 'SoulDiaryConnectApp/analisi_paziente.html', {
        'medico': medico,
        'paziente': paziente_selezionato,
        'emotion_chart_data': emotion_chart_data,
        'statistiche': statistiche,
        'note_diario': note_diario,
        'correlazione_contesto_data': correlazione_contesto_data,
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

