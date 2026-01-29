# SoulDiaryConnect

**SoulDiaryConnect** is an AI-powered system designed to support patients in their **psychotherapeutic journey** by enabling journaling with **personalized AI feedback**, while keeping the **therapist connected and in control**. 
The platform allows patients to **log daily experiences**, receive **AI-generated motivational and clinical feedback**, and stay in touch with their physician.
The AI used is [Llama 3.1:8B](https://ollama.com/library/llama3.1:8b), running locally via [Ollama](https://ollama.com/).
<p align="center">
  <img src="https://github.com/FLaTNNBio/SoulDiaryConnect2.0/blob/main/media/2-01.png" width="250" alt="Logo SoulDiaryConnect">
</p>

---

## Features

- **AI-Assisted Journaling** – Patients can document their daily experiences and receive **motivational feedback** from an LLM.
- **Personalized AI** – Doctors can **configure AI responses** to provide **clinical insights** and tailor support to each patient.
- **Intuitive User Interface** – A web application with **dedicated patient and doctor dashboards**.
- **Secure Data Management** – Uses **PostgreSQL** for structured data storage.
- **Advanced NLP Processing** – Powered by **Llama 3.1:8B**, running locally with **Ollama**.
- **Multi-User Access** – Patients and doctors have separate roles and functionalities.

---

## Tech Stack

- **Backend**: Django
- **Frontend**: HTML, CSS, JavaScript
- **NLP**: Llama 3.1:8B via Ollama
- **Database**: PostgreSQL

---

## Installation Guide

### **1️. Clone the repository**
```sh
git clone https://github.com/FLaTNNBio/SoulDiaryConnect2.0.git
cd SoulDiaryConnect2.0
```

### **2. Set up a virtual environment**
```sh
python3 -m venv venv
source venv/bin/activate  # on Windows: venv\Scripts\activate
```

### **3. Install dependencies**
```sh
pip install -r requirements.txt
```

## **4. Configure the database**

Install PostgreSQL following the [official guideline](https://www.postgresql.org/download/).<br>
To exectute the queries:
```sh
python manage.py dbshell
```

Then:
```sql
\i souldiaryconnect.sql
```

Now edit **setting.py** to configure PostgreSQL:
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'souldiaryconnect',
        'USER': 'your_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

Make database migrations:
```sh
python manage.py makemigrations
```

Run database migrations:
```sh
python manage.py migrate
```

## **5. Install and configure Ollama**

### **5.1 Install Ollama**

Download and install Ollama from the [official website](https://ollama.com/download):

- **Windows**: Download the installer and follow the setup wizard
- **macOS**: `brew install ollama` or download from the website
- **Linux**: `curl -fsSL https://ollama.com/install.sh | sh`

### **5.2 Download the Llama 3.1:8B model**

Once Ollama is installed, open a terminal and run:

```sh
ollama pull llama3.1:8b
```

This will download the Llama 3.1:8B model (~4.7GB).

### **5.3 Verify Ollama is running**

Start the Ollama service (it usually starts automatically after installation):

```sh
ollama serve
```

Verify it's working:

```sh
ollama list
```

You should see `llama3.1:8b` in the list of available models.

> **Note**: Ollama runs on `http://localhost:11434` by default. The application is configured to connect to this endpoint automatically.

## **6. Start the server**
```sh
python manage.py runserver
```
## **Roles & Functionality**
### Doctor
- **Manage patients** – Access and review patient journal entries.
- **Customize AI responses** – Configure the AI to tailor feedback generation.
- **Monitor therapy progress** – View clinical trends and intervene when necessary.
### Patient
- **Write personal journal entries** – Document daily thoughts and emotions.
- **Receive AI-generated feedback** – Get motivational and therapeutic insights.
- **View therapist's comments** – See personalized feedback from the doctor.
