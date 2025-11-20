from django.shortcuts import render, redirect, get_object_or_404
from .models import Medico, Paziente, NotaDiario
from django.contrib import messages
from django.contrib.auth import logout
from datetime import datetime
import requests
import logging
import re

logger = logging.getLogger(__name__)

# Configurazione Ollama
OLLAMA_BASE_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1:8b"  # Cambia in "cbt-assistant" se hai creato il modello personalizzato


def genera_con_ollama(prompt, max_tokens=150, temperature=0.7):
    """
    Funzione helper per chiamare Ollama API e normalizzare la risposta rimuovendo
    eventuali prefissi o etichette introduttive (es. "Risposta:", "La tua risposta:").
    """
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }

        response = requests.post(OLLAMA_BASE_URL, json=payload, timeout=120)
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

        # Rimuove virgolette, apici, bullets o caratteri di maggiore iniziali
        text = re.sub(r'^[\'"“«\s\-\u2022>]+', '', text).strip()

        return text

    except requests.exceptions.RequestException as e:
        logger.error(f"Errore nella chiamata a Ollama: {e}")
        return f"Errore durante la generazione: {e}"


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
            codice_identificativo = request.POST['codice_identificativo']
            indirizzo_studio = request.POST['indirizzo_studio']
            citta = request.POST['citta']
            numero_civico = request.POST['numero_civico']
            numero_telefono_studio = request.POST.get('numero_telefono_studio')
            numero_telefono_cellulare = request.POST.get('numero_telefono_cellulare')

            Medico.objects.create(
                codice_identificativo=codice_identificativo,
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

    return render(request, 'SoulDiaryConnectApp/register.html')


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

    prompt = f"""
        You are a supportive assistant. Use the following example to craft your response.

        Example:
        Text: "I failed my exam and feel like giving up."
        Response: "I'm so sorry to hear about your exam. It's okay to feel disappointed, but this doesn't define your worth. Consider revising your study strategy and asking for help. You've got this!"

        Now, respond to the following text in italian:
        {testo}
        """

    return genera_con_ollama(prompt, max_tokens=350, temperature=0.3)


def genera_frasi_cliniche(testo, medico):
    """
    Genera note cliniche CBT personalizzate in base alle preferenze del medico
    """
    print("Generazione commenti clinici con Ollama")

    try:
        # Determina i parametri dal medico
        tipo_nota = medico.tipo_nota  # True per "strutturato", False per "non strutturato"
        lunghezza_nota = medico.lunghezza_nota  # True per "lungo", False per "breve"
        tipo_parametri = medico.tipo_parametri.split(".:;!") if medico.tipo_parametri else []
        testo_parametri = medico.testo_parametri.split(".:;!") if medico.testo_parametri else []

        # Determina il max_tokens in base alla lunghezza_nota
        max_tokens = 300 if lunghezza_nota else 150

        if tipo_nota:
            # Genera il prompt strutturato con parametri
            parametri_strutturati = "\n".join(
                [f"{tipo}: {testo}" for tipo, testo in zip(tipo_parametri, testo_parametri)]
            )

            prompt = f"""
                        You are a psychotherapist specializing in CBT. Analyze the following text and provide a clinical assessment. Respond only in Italian.

                        Example:
                        Text: "Today I failed my exam and feel like giving up."
                        Response: 
                        {parametri_strutturati}

                        Parameters:
                        {tipo_parametri}

                        Now analyze this text:
                        {testo}

                        Respond in the format of the example response:
                        """
        else:
            # Genera il prompt non strutturato
            prompt = f"""
                        You are a psychotherapist specializing in CBT. Analyze the following text and provide a clinical assessment. Respond only in Italian. The text is: {testo}
                        """

        return genera_con_ollama(prompt, max_tokens=350, temperature=0.6)

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

        if testo_paziente:
            testo_supporto = genera_frasi_di_supporto(testo_paziente)
            testo_clinico = genera_frasi_cliniche(testo_paziente, medico)

            NotaDiario.objects.create(
                paz=paziente,
                testo_paziente=testo_paziente,
                testo_supporto=testo_supporto,
                testo_clinico=testo_clinico,
                data_nota=datetime.now()
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
