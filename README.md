# 🚀 Policy Pilot — Offline AI Government Scheme Assistant

> Your intelligent, fully offline navigator to discover **3400+ government schemes** — powered by local AI.

---

## 🌟 Overview

**Policy Pilot** is an AI-powered assistant that helps users discover government schemes based on their personal profile — such as age, state, occupation, and income.

Unlike traditional platforms, Policy Pilot runs **completely offline**, powered by:

* 🧠 Local LLM via **Ollama**
* 🗄️ Structured database using **SQLite**
* 📊 A curated dataset of **3400+ government schemes**

---

## 🎯 Problem Statement

Millions of eligible citizens miss out on government benefits due to:

* Fragmented information across portals
* Complex eligibility criteria
* Lack of personalization
* Poor internet connectivity in rural areas

---

## 💡 Solution

Policy Pilot solves this by:

* Understanding user queries in natural language
* Running a **local AI model (Ollama)** to interpret intent
* Querying a **SQLite database of 3400+ schemes**
* Delivering **instant, personalized recommendations — without internet**

---

## 🔥 Key Features

### 🧠 Local AI (Ollama Integration)

* Uses **Ollama** to run LLM locally
* No external APIs required
* Zero latency from network calls
* Fully private and secure

---

### 🗄️ SQLite Database (Structured Data)

* 3400+ government schemes stored locally
* Efficient querying using SQL
* Structured fields:

  * Eligibility
  * Benefits
  * State
  * Category
  * Income limits

---

### 📦 100% Offline System

* Works without internet
* Ideal for rural deployment
* No dependency on cloud APIs

---

### 💬 Smart Chat Interface

* Natural language input
* Chat-based interaction
* Suggestion-based quick inputs

---

### 🎯 Intelligent Matching System

* Combines:

  * Rule-based filtering (SQL)
  * AI interpretation (Ollama)
* Provides highly relevant results

---

### 🔒 Privacy First

* No data leaves the device
* No API calls
* सुरक्षित and user-friendly

---

## 🧩 Tech Stack

| Layer     | Technology                     |
| --------- | ------------------------------ |
| Frontend  | HTML, Tailwind CSS, JavaScript |
| Backend   | Python (Flask)                 |
| AI Engine | Ollama (Local LLM)             |
| Database  | SQLite                         |
| Data      | 3400+ Government Schemes       |

---

## ⚙️ Architecture

```id="arch001"
User Input (Natural Language)
        ↓
Ollama (Local LLM)
        ↓
Intent Extraction (age, state, income, etc.)
        ↓
SQLite Query Engine
        ↓
Filtered Schemes
        ↓
Response Generation (AI formatted)
        ↓
UI Display (Chat Interface)
```

---

## 📁 Project Structure

```id="struct001"
Policy-Pilot/
│
├── index.html          # Frontend UI (single file)
├── app.py              # Flask backend
├── database.db         # SQLite database (3400+ schemes)
├── ollama_config/      # Model setup / prompts
├── README.md
```

---

## 🚀 Getting Started

### 1️⃣ Install Ollama

```id="cmd001"
curl -fsSL https://ollama.com/install.sh | sh
```

Run a model:

```id="cmd002"
ollama run llama3
```

---

### 2️⃣ Run Backend

```id="cmd003"
python app.py
```

---

### 3️⃣ Open UI

Open:

```id="cmd004"
index.html
```

---

## 🧪 Example Queries

* “I am a farmer in Gujarat with income below ₹1.5 lakh”
* “Widow woman with BPL card in Rajasthan”
* “Student from Maharashtra with low income”

---

## 📈 Future Enhancements

* 🗺️ Nearby scheme centers (map integration)
* 📊 Admin dashboard with analytics
* 🤖 ML-based ranking (XGBoost)
* 🌐 Multi-language expansion (10+ languages)
* 📱 Mobile app

---

## 🏆 Why This Project Stands Out

* ✅ Fully offline AI system
* ✅ No API dependency (rare + powerful)
* ✅ Local LLM (Ollama integration)
* ✅ Real-world usable dataset (3400+ schemes)
* ✅ Scalable for national deployment

---

## 👨‍💻 Built By

<p align="center">
  <table>
    <tr>
      <td align="center" width="25%">
        <div>
          <img src="https://avatars.githubusercontent.com/Sam-bot-dev?s=120" width="120px;" height="120px;" alt="Bhavesh"/>
        </div>
        <div><strong>🧩 Head Teammate</strong></div>
        <div><strong>Bhavesh</strong></div>
        <a href="https://github.com/Sam-bot-dev">🌐 GitHub</a>
      </td>
      <td align="center" width="25%">
        <div>
          <img src="https://avatars.githubusercontent.com/notUbaid?s=120" width="120px;" height="120px;" alt="Ubaid khan"/>
        </div>
        <div><strong>⭐ Team Leader</strong></div>
        <div><strong>Ubaid khan</strong></div>
        <a href="https://github.com/niyatijoshi707-ai">🌐 GitHub</a>
      </td>
      <td align="center" width="25%">
        <div>
          <img src="https://avatars.githubusercontent.com/rhn9999?s=120" width="120px;" height="120px;" alt="Rohan"/>
        </div>
        <div><strong>Teammate</strong></div>
        <div><strong>Kush</strong></div>
        <a href="https://github.com/rhn9999">🌐 GitHub</a>
      </td>
      <td align="center" width="25%">
        <div>
          <img src="https://avatars.githubusercontent.com/Codieeri?s=120" width="120px;" height="120px;" alt="Yug"/>
        </div>
        <div><strong>Teammate</strong></div>
        <div><strong>Riya</strong></div>
        <a href="https://github.com/Codieeri">🌐 GitHub</a>
      </td>
    </tr>
  </table>
</p>

---

## 💬 Tagline

> “Policy Pilot — AI that works for everyone, even without the internet.”

---

## 📜 License

Open for educational and innovation purposes.
