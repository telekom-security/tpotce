# T-Pot 🍯 - MacOS🍎 Installation & Testing 📊

## Table of Contents
- [Introduction](#introduction)
  - [Features and Benefits](#features-and-benefits)
  - [Architecture](#architecture)
  - [Supported Honeypots](#supported-honeypots)
  - [Tools Included](#tools-included)
- [MacOS Installation](#macos-installation)
  - [Required Ports](#required-ports)
  - [Uninstall T-Pot](#uninstall-tpot)
- [Data Analysis and Insights](#data-analysis-and-insights)
- [Conclusion](#conclusion)

---
<a name="introduction"></a>
## 1. Introduction 🌍
**T-Pot** is an all-in-one honeypot platform designed by Deutsche Telekom. It supports multi-architectures (amd64, arm64) and offers a wide range of visualization options using the **Elastic Stack**, real-time animated attack maps, and numerous security tools to enhance the deception experience. 🍯

---
<a name="features-and-benefits"></a>
### 1.1 Features and Benefits 💡
T-Pot provides several key features that make it a powerful tool for cybersecurity professionals and researchers:

- **Comprehensive Honeypot Integration**: T-Pot combines over 20 honeypots, each designed to capture different types of malicious activity. This integration allows for monitoring and analyzing a wide variety of attack vectors.
  
- **Elastic Stack Integration**: The platform includes the **ELK stack** (Elasticsearch, Logstash, and Kibana), facilitating data collection, analysis, and visualization. This integration offers powerful tools for real-time threat intelligence.

- **Docker and Docker Compose**: Using Docker and Docker Compose, T-Pot simplifies deployment and management. Each honeypot runs in its own container, ensuring isolation and ease of maintenance.

- **Advanced Visualization Tools**: T-Pot provides tools like **CyberChef**, **Elasticvue**, and a real-time attack map, making it easy to interpret and understand the data collected by the honeypots.

- **Scalability and Flexibility**: T-Pot can be deployed on multiple Linux distributions, macOS, and Windows (with limited functionality). It can run on physical hardware, virtual machines, or cloud environments like AWS.

- **Community Data Sharing**: By default, T-Pot sends data to the **Sicherheitstacho** community backend, contributing to collective threat intelligence. This feature can be disabled if needed.

---
<a name="architecture"></a>
### 1.2 Architecture 🏗️
The core components of T-Pot have been moved into a Docker image called **tpotinit**. This change has made T-Pot compatible with multiple Linux distributions, macOS, and Windows (with some limitations due to Docker Desktop). T-Pot uses **Docker** and **Docker Compose** to run as many honeypots and tools as possible simultaneously, maximizing the host's hardware utilization.

---
<a name="supported-honeypots"></a>
### 1.3 Supported Honeypots 🛡️
T-Pot supports a wide range of honeypots, including:

#### 1.3.1 Industrial and Medical Honeypots 🏭
1. **[Conpot](http://conpot.org/)**: Simulates Industrial Control Systems (ICS) and protocols like Modbus, SNMP, and S7comm.
2. **[Dicompot](https://github.com/nsmfoo/dicompot)**: Emulates medical imaging systems (DICOM) to detect attacks on medical devices.
3. **[medpot](https://github.com/schmalle/medpot)**: Simulates medical data management systems, focusing on healthcare sector attacks.

#### 1.3.2 Network and IoT Honeypots 🌐
1. **[Adbhoney](https://github.com/huuck/ADBHoney)**: Simulates Android devices exposed via the ADB (Android Debug Bridge) protocol.
2. **[Ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot)**: Emulates Cisco ASA devices to detect attacks on firewalls and VPNs.
3. **[Citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot)**: Simulates known Citrix vulnerabilities, such as CVE-2019-19781.
4. **[Dionaea](https://github.com/DinoTools/dionaea)**: Emulates vulnerable network services (e.g., SMB, FTP) to capture malware and exploits.
5. **[Endlessh](https://github.com/skeeto/endlessh)**: Simulates an SSH server that keeps connections open indefinitely, slowing down network scanners.
6. **[Ipphoney](https://gitlab.com/bontchev/ipphoney)**: Emulates IPP (Internet Printing Protocol) services to detect attacks on network printers.

#### 1.3.3 Web and Application Honeypots 🌍
1. **[Cowrie](https://github.com/cowrie/cowrie)**: Emulates SSH and Telnet servers to capture brute-force attempts and malicious commands.
2. **[Hellpot](https://github.com/yunginnanet/HellPot)**: Simulates vulnerable HTTP servers to capture "log4shell" attacks (CVE-2021-44228).

#### 1.3.4 DDoS and Anomaly Detection Honeypots ⚠️
1. **[Ddospot](https://github.com/aelth/ddospot)**: Detects and analyzes DDoS attacks by simulating vulnerable services.
2. **[Honeytrap](https://github.com/armedpot/honeytrap/)**: Monitors network traffic and dynamically launches honeypots based on incoming requests.

#### 1.3.5 Email and Communication Honeypots 📧
1. **[Mailoney](https://github.com/awhitehatter/mailoney)**: Emulates SMTP servers to capture spam and phishing attempts.
2. **[Heralding](https://github.com/johnnykv/heralding)**: Simulates authentication services (e.g., SSH, FTP) to capture stolen credentials.

#### 1.3.6 Malware and Advanced Analysis Honeypots 🦠
1. **[Beelzebub](https://github.com/mariocandela/beelzebub)**: Analyzes malware by emulating vulnerable services.
2. **[snare](http://mushmush.org/)** / **[tanner](http://mushmush.org/)**: Snare captures interactions, while Tanner analyzes attacker behavior.
3. 

#### 1.3.7 Data Traps and Advanced Deception Honeypots 🎯
1. **[Elasticpot](https://gitlab.com/bontchev/elasticpot)**: Simulates an unprotected Elasticsearch server, often targeted for data breaches.
2. **[H0neytr4p](https://github.com/pbssubhash/h0neytr4p)**: A generic honeypot for capturing interactions with exposed services.

---
<a name="tools-included"></a>
### 1.4 Tools Included 🛠️
T-Pot also includes the following tools:
- **Autoheal**: Automatically restarts containers with failed health checks.
- **CyberChef**: A web app for encryption, encoding, compression, and data analysis.
- **Elastic Stack**: For beautifully visualizing all events captured by T-Pot.
- **Elasticvue**: A web frontend for browsing and interacting with an Elasticsearch cluster.
- **Fatt**: A PyShark-based script for extracting network metadata and fingerprints from PCAP files and live traffic.
- **T-Pot Attack Map**: A beautifully animated attack map for T-Pot.
- **P0f**: A tool for purely passive traffic fingerprinting.
- **Spiderfoot**: An open-source intelligence automation tool.
- **Suricata**: A Network Security Monitoring engine.

---
<a name="macos-installation"></a>
## 2 MacOS Installation 🍏
As Docker Desktop is rather limited not all honeypot types or T-Pot features are supported. Also remember, by default the macOS are blocking access from remote, so testing is limited to the host.

To get things up and running just follow these steps:
1. Install Docker Desktop for [macOS](https://docs.docker.com/desktop/install/mac-install/)
2. Clone the GitHub repository:
   ```
   git clone https://github.com/domedg/tpotce_MacOS/
   ```
4. Go to : `cd tpotce_MacOS/`
5. Copy `cp compose/mac_win.yml ./docker-compose.yml`
6. Check if the script "genuser.sh" is executable, if is not run `chmod 777 genuser.sh`
7. Create a `WEB_USER` by running `./genuser.sh`
   
8. Adjust the `.env` file by changing `TPOT_OSTYPE=linux` to either `mac` or `win`:
9. You have to ensure on your own there are no port conflicts keeping T-Pot from starting up.
10. Start T-Pot: `docker compose up` or `docker compose up -d` if you want T-Pot to run in the background.
11. Stop T-Pot: `CTRL-C` (it if was running in the foreground) and / or `docker compose down -v` to stop T-Pot entirely.

---
<a name="required-ports"></a>
### 2.1 Required Ports 🔌
Besides the ports generally needed by the OS, i.e. obtaining a DHCP lease, DNS, etc. T-Pot will require the following ports for incoming / outgoing connections. Review the [T-Pot Architecture](#technical-architecture) for a visual representation. Also some ports will show up as duplicates, which is fine since used in different editions.

| Port                                                                                                                                  | Protocol | Direction | Description                                                                                         |
| :------------------------------------------------------------------------------------------------------------------------------------ | :------- | :-------- | :-------------------------------------------------------------------------------------------------- |
| 80, 443                                                                                                                               | tcp      | outgoing  | T-Pot Management: Install, Updates, Logs (i.e. OS, GitHub, DockerHub, Sicherheitstacho, etc.        |
| 11434                                                                                                                                 | tcp      | outgoing  | LLM based honeypots: Access your Ollama installation                                                |
| 64294                                                                                                                                 | tcp      | incoming  | T-Pot Management: Sensor data transmission to hive (through NGINX reverse proxy) to 127.0.0.1:64305 |
| 64295                                                                                                                                 | tcp      | incoming  | T-Pot Management: Access to SSH                                                                     |
| 64297                                                                                                                                 | tcp      | incoming  | T-Pot Management Access to NGINX reverse proxy                                                      |
| 5555                                                                                                                                  | tcp      | incoming  | Honeypot: ADBHoney                                                                                  |
| 22                                                                                                                                    | tcp      | incoming  | Honeypot: Beelzebub  (LLM required)                                                                 |
| 5000                                                                                                                                  | udp      | incoming  | Honeypot: CiscoASA                                                                                  |
| 8443                                                                                                                                  | tcp      | incoming  | Honeypot: CiscoASA                                                                                  |
| 443                                                                                                                                   | tcp      | incoming  | Honeypot: CitrixHoneypot                                                                            |
| 80, 102, 502, 1025, 2404, 10001, 44818, 47808, 50100                                                                                  | tcp      | incoming  | Honeypot: Conpot                                                                                    |
| 161, 623                                                                                                                              | udp      | incoming  | Honeypot: Conpot                                                                                    |
| 22, 23                                                                                                                                | tcp      | incoming  | Honeypot: Cowrie                                                                                    |
| 19, 53, 123, 1900                                                                                                                     | udp      | incoming  | Honeypot: Ddospot                                                                                   |
| 11112                                                                                                                                 | tcp      | incoming  | Honeypot: Dicompot                                                                                  |
| 21, 42, 135, 443, 445, 1433, 1723, 1883, 3306, 8081                                                                                   | tcp      | incoming  | Honeypot: Dionaea                                                                                   |
| 69                                                                                                                                    | udp      | incoming  | Honeypot: Dionaea                                                                                   |
| 9200                                                                                                                                  | tcp      | incoming  | Honeypot: Elasticpot                                                                                |
| 22                                                                                                                                    | tcp      | incoming  | Honeypot: Endlessh                                                                                  |
| 80, 443, 8080, 8443                                                                                                                   | tcp      | incoming  | Honeypot: Galah  (LLM required)                                                                     |
| 8080                                                                                                                                  | tcp      | incoming  | Honeypot: Go-pot                                                                                    |
| 80, 443                                                                                                                               | tcp      | incoming  | Honeypot: H0neytr4p                                                                                 |
| 21, 22, 23, 25, 80, 110, 143, 443, 993, 995, 1080, 5432, 5900                                                                         | tcp      | incoming  | Honeypot: Heralding                                                                                 |
| 3000                                                                                                                                  | tcp      | incoming  | Honeypot: Honeyaml                                                                                  |
| 21, 22, 23, 25, 80, 110, 143, 389, 443, 445, 631, 1080, 1433, 1521, 3306, 3389, 5060, 5432, 5900, 6379, 6667, 8080, 9100, 9200, 11211 | tcp      | incoming  | Honeypot: qHoneypots                                                                                |
| 53, 123, 161, 5060                                                                                                                    | udp      | incoming  | Honeypot: qHoneypots                                                                                |
| 631                                                                                                                                   | tcp      | incoming  | Honeypot: IPPHoney                                                                                  |
| 80, 443, 8080, 9200, 25565                                                                                                            | tcp      | incoming  | Honeypot: Log4Pot                                                                                   |
| 25                                                                                                                                    | tcp      | incoming  | Honeypot: Mailoney                                                                                  |
| 2575                                                                                                                                  | tcp      | incoming  | Honeypot: Medpot                                                                                    |
| 9100                                                                                                                                  | tcp      | incoming  | Honeypot: Miniprint                                                                                 |
| 6379                                                                                                                                  | tcp      | incoming  | Honeypot: Redishoneypot                                                                             |
| 5060                                                                                                                                  | tcp/udp  | incoming  | Honeypot: SentryPeer                                                                                |
| 80                                                                                                                                    | tcp      | incoming  | Honeypot: Snare (Tanner)                                                                            |
| 8090                                                                                                                                  | tcp      | incoming  | Honeypot: Wordpot                                                                                   |

---
<a name="uninstall-tpot"></a>
### 2.2 Uninstall T-Pot 🧹
Uninstallation of T-Pot is only available on the [supported Linux distros](#choose-your-distro).<br>
To uninstall T-Pot run ~/tpotce/uninstall.sh and follow the uninstaller instructions, you will have to enter your password at least once.<br>
Once the uninstall is finished reboot the machine sudo reboot
<br><br>

---

<a name="data-analysis-and-insights"></a>
## 3 Data Analysis and Insights 📊
Recent studies, such as one conducted by **Jiuma Elhshik**, have demonstrated T-Pot's effectiveness in collecting and analyzing threat data. Over 48 hours, T-Pot captured **126,833 attacks**, providing valuable insights into current threat landscapes. Key findings include:

1. **Most Targeted Honeypots**:
   - **Dionaea**: Over 47,000 attacks, primarily targeting SMB (port 445).
   - **DDospot**: Specialized in detecting DDoS attacks.
   - **Honeytrap**: Attracted a wide range of attacks.

2. **Geographical Origin of Attacks**:
   - Most attacks originated from the **United States** and **China**, with significant activity from **Iran** and the **Netherlands**. Note that IP spoofing may obscure true origins.

3. **Exploited Vulnerabilities**:
   - **CVE-2023-50387 (KeyTrap)**: Targets DNS servers.
   - **CVE-2023-46604**: A deserialization vulnerability in Apache ActiveMQ.

4. **Attack Techniques**:
   - Brute-force attempts on SSH and Telnet services.
   - Use of backdoors like **DoublePulsar**.
   - Detection of malware such as **Hajime**, a worm known for creating botnets.

For more detailed information, you can read the full study by Jiuma Elhshik on [Medium](https://medium.com/@jiumael/trap-the-hackers-building-and-analyzing-a-t-pot-honeypot-b15f3b6c5ea2).

---
<a name="conclusion"></a>
## 4 Conclusion 🔚
T-Pot is a powerful and versatile platform for cybersecurity professionals and researchers. Its ability to integrate multiple honeypots, provide advanced visualization tools, and scale across different environments makes it an essential tool for understanding and mitigating cyber threats. By contributing to collective threat intelligence, T-Pot helps build a safer digital world. 🌐🔒

---
