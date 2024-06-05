# HoneyPot Data collector

HoneyPot Data collector is software intended to collect information from all TPot CE honey pots, deployed in DAEU.

## Requirements

 - Python 3.9
 - PostgreSQL (developed on 13.4, but should work on any)

## Installation

1. Clone repo

```bash
git clone https://github.com/nu11secur1ty/tpotce/tree/master

cd /tpotce/collector
```

2. Install requirements

```
cd HonePotCollector 
pip install -r requirements.txt
```

3. Config SQL user
4. Make migrations
```
python3 manage.py migrate
```

5. Create super user
```
python manage.py createsuperuser
```
6. Start server as daemon
```
nohup python3 manage.py runserver 0.0.0.0:8000 > ~/collector.log &
```

## Configuration

### SSH Keys

Please create private/public key pair for the user you are going to run the server. Copy public keys to all machines you are going to scrape.

### Add target servers

Go to `http://<serverip>:8000/admin` and login with superuser. 

Go to "Collectorapi" > "Honey pot servers" and add servers, with IPs (use `0000` as key to generate random key)

### Automate

Add crontab to run `collector.sh` each 15 minutes (+1 just in case)

```
1,16,31,46 * * * * /bin/bash /home/collector/HoneyPotCollector/collector.sh 1> /home/collector/cron.log
```

# API Documentation

## CollectorAPI

### /API/targets

Get list of all active IPs we have to crawl

**Responds only on localhost requests**

### /API/from_time

Get from time for the current request

**Responds only on localhost requests**

### /API/to_time

Get to time for the current request

**Responds only on localhost requests**

### /API/post_local

Local post instance. Accepts only from localhost and is used to load the JSON files we get with the collector scripts.

**Responds only on localhost requests**

### /API/post

Remote post instance. Accepts only from verified active keys and servers. Loads the remote send JSON files.

### /API/report/ips

Returns JSON formatted list of results related to attacking IPs

Accepts the following GET paramaters:
    
- **days** (int) - how many hours back should the report return
- **limit** (int)- How many results should it return
- **iso** (str) - for which country should results be returned
- **no_count** - boolen for should the attack count be returned

### /API/report/countries

Returns JSON formatted list of results related to attacking countries

Accepts the following GET paramaters:
    
- **days** (int) - how many hours back should the report return
- **limit** (int)- How many results should it return
- **no_count** - boolen for should the attack count be returned

### /API/report/protocols

Returns JSON formatted list of results related to attacked protocols

Accepts the following GET paramaters:
    
- **days** (int) - how many hours back should the report return
- **limit** (int)- How many results should it return
- **no_count** - boolen for should the attack count be returned
