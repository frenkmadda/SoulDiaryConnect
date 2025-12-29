from django.shortcuts import render, redirect, get_object_or_404
from .models import Medico, Paziente, NotaDiario
from django.contrib import messages
from django.contrib.auth import logout
from django.utils import timezone
import requests
import logging
import re
import json
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# Configurazione Ollama
OLLAMA_BASE_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1:8b"  # Cambia in "cbt-assistant" se hai creato il modello personalizzato

# Configurazione lunghezza note cliniche (in caratteri)
LUNGHEZZA_NOTA_BREVE = 350
LUNGHEZZA_NOTA_LUNGA = 800


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
    Analizza il sentiment del testo del paziente e restituisce l'emozione predominante con emoji.
    """
    print("Analisi sentiment con Ollama")

    emozioni_lista = ', '.join(EMOZIONI_EMOJI.keys())
    
    prompt = f"""Sei un esperto di analisi delle emozioni. Il tuo compito è identificare l'emozione predominante in un testo.

ISTRUZIONI:
- Analizza attentamente il testo fornito
- Identifica l'emozione principale espressa
- Rispondi con UNA SOLA PAROLA: l'emozione predominante
- Le emozioni possibili sono: {emozioni_lista}
- Se non riesci a identificare un'emozione specifica, rispondi con l'emozione più vicina
- Non aggiungere spiegazioni, punteggiatura o altro

Esempi:
Testo: "Oggi sono riuscito a superare l'esame, sono contentissimo!"
Risposta: gioia

Testo: "Mi sento solo e nessuno mi capisce"
Risposta: solitudine

Testo: "Non ce la faccio più, tutto va storto"
Risposta: frustrazione

Testo da analizzare:
{testo}

Emozione predominante:"""

    risposta = genera_con_ollama(prompt, max_chars=50, temperature=0.1)
    
    # Normalizza la risposta: lowercase e rimuovi spazi
    emozione = risposta.lower().strip().rstrip('.')
    
    # Cerca una corrispondenza nel dizionario
    for chiave in EMOZIONI_EMOJI.keys():
        if chiave in emozione:
            return chiave
    
    # Se non trova corrispondenza, restituisci la risposta così com'è
    # con un'emoji di default
    return emozione if emozione else 'neutro'


def get_emoji_for_emotion(emozione):
    """
    Restituisce l'emoji corrispondente all'emozione.
    Se l'emozione non è nel dizionario, restituisce un'emoji di default.
    """
    if not emozione:
        return '💭'
    emozione_lower = emozione.lower().strip()
    return EMOZIONI_EMOJI.get(emozione_lower, '💭')


def _genera_prompt_strutturato_breve(testo, parametri_strutturati, tipo_parametri, max_chars):
    """Prompt per nota strutturata breve"""
    return f"""Sei un assistente per uno psicoterapeuta. Analizza il seguente testo e fornisci una valutazione clinica strutturata e CONCISA.

Esempio:
Testo: "Oggi ho fallito il mio esame e ho voglia di arrendermi."
Risposta:
{parametri_strutturati}

Parametri da utilizzare:
{tipo_parametri}

ISTRUZIONI IMPORTANTI:
- La risposta deve essere BREVE e SINTETICA (massimo {max_chars} caratteri)
- FORMATO OBBLIGATORIO: ogni parametro deve essere su una NUOVA RIGA nel formato "NomeParametro: valore"
- Vai a capo dopo ogni parametro
- Non usare markdown, elenchi puntati o simboli
- Scrivi solo in italiano
- Completa sempre la frase, non troncare mai a metà
- NON usare frasi introduttive come "Ecco la nota clinica", "Ecco l'analisi", "Di seguito" o simili
- Inizia DIRETTAMENTE con il primo parametro

Ora analizza questo testo:
{testo}"""


def _genera_prompt_strutturato_lungo(testo, parametri_strutturati, tipo_parametri, max_chars):
    """Prompt per nota strutturata lunga"""
    return f"""Sei un assistente per uno psicoterapeuta. Analizza il seguente testo e fornisci una valutazione clinica strutturata e DETTAGLIATA.

Esempio:
Testo: "Oggi ho fallito il mio esame e ho voglia di arrendermi."
Risposta:
{parametri_strutturati}

Parametri da utilizzare:
{tipo_parametri}

ISTRUZIONI IMPORTANTI:
- La risposta deve essere DETTAGLIATA e APPROFONDITA (massimo {max_chars} caratteri)
- FORMATO OBBLIGATORIO: ogni parametro deve essere su una NUOVA RIGA nel formato "NomeParametro: valore"
- Vai a capo dopo ogni parametro
- Fornisci analisi complete per ogni parametro
- Non usare markdown, elenchi puntati o simboli
- Scrivi solo in italiano
- Completa sempre la frase, non troncare mai a metà
- NON usare frasi introduttive come "Ecco la nota clinica", "Ecco l'analisi", "Di seguito" o simili
- Inizia DIRETTAMENTE con il primo parametro

Ora analizza questo testo:
{testo}"""


def _genera_prompt_non_strutturato_breve(testo, max_chars):
    """Prompt per nota non strutturata breve"""
    return f"""Sei un assistente di uno psicoterapeuta specializzato. Analizza il seguente testo e fornisci una valutazione clinica discorsiva BREVE.

ISTRUZIONI IMPORTANTI:
- La risposta deve essere BREVE e SINTETICA (massimo {max_chars} caratteri)
- Scrivi in modo discorsivo, senza struttura a punti
- Non usare elenchi, grassetti, markdown, simboli o titoli
- NON usare frasi introduttive come "Ecco la nota clinica", "Ecco l'analisi", "Di seguito", "La valutazione è" o simili
- Inizia DIRETTAMENTE con l'analisi del contenuto emotivo/psicologico
- Scrivi solo in italiano
- Completa sempre la frase, non troncare mai a metà

Testo da analizzare:
{testo}"""


def _genera_prompt_non_strutturato_lungo(testo, max_chars):
    """Prompt per nota non strutturata lunga"""
    return f"""Sei un assistente di uno psicoterapeuta specializzato. Analizza il seguente testo e fornisci una valutazione clinica discorsiva DETTAGLIATA e APPROFONDITA.

ISTRUZIONI IMPORTANTI:
- La risposta deve essere DETTAGLIATA e COMPLETA (massimo {max_chars} caratteri)
- Scrivi in modo discorsivo, senza struttura a punti
- Approfondisci gli aspetti emotivi, cognitivi e comportamentali
- Non usare elenchi, grassetti, markdown, simboli o titoli
- NON usare frasi introduttive come "Ecco la nota clinica", "Ecco l'analisi", "Di seguito", "La valutazione è" o simili
- Inizia DIRETTAMENTE con l'analisi del contenuto emotivo/psicologico
- Scrivi solo in italiano
- Completa sempre la frase, non troncare mai a metà

Testo da analizzare:
{testo}"""


def genera_frasi_cliniche(testo, medico):
    """
    Genera note cliniche personalizzate in base alle preferenze del medico.
    
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

        if tipo_nota:
            # Nota strutturata
            parametri_strutturati = "\n".join(
                [f"{tipo}: {txt}" for tipo, txt in zip(tipo_parametri, testo_parametri)]
            )
            if lunghezza_nota:
                # Strutturata + Lunga
                prompt = _genera_prompt_strutturato_lungo(testo, parametri_strutturati, tipo_parametri, max_chars)
            else:
                # Strutturata + Breve
                prompt = _genera_prompt_strutturato_breve(testo, parametri_strutturati, tipo_parametri, max_chars)
        else:
            # Nota non strutturata
            if lunghezza_nota:
                # Non Strutturata + Lunga
                prompt = _genera_prompt_non_strutturato_lungo(testo, max_chars)
            else:
                # Non Strutturata + Breve
                prompt = _genera_prompt_non_strutturato_breve(testo, max_chars)

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

        if testo_paziente:
            if generate_response_flag:
                testo_supporto = genera_frasi_di_supporto(testo_paziente)

            testo_clinico = genera_frasi_cliniche(testo_paziente, medico)
            emozione_predominante = analizza_sentiment(testo_paziente)

            NotaDiario.objects.create(
                paz=paziente,
                testo_paziente=testo_paziente,
                testo_supporto=testo_supporto,
                testo_clinico=testo_clinico,
                emozione_predominante=emozione_predominante,
                data_nota=timezone.now()
            )

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
            testo_paziente = nota.testo_paziente
            nuova_frase = genera_frasi_cliniche(testo_paziente, medico)
            # Sostituisci la frase clinica precedente
            nota.testo_clinico = nuova_frase
            nota.save(update_fields=["testo_clinico"])
            return JsonResponse({'testo_clinico': nuova_frase})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Richiesta non valida.'}, status=400)


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


