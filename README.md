# Zepp to Endurain Sync

Script to automatically sync your Zepp (Huami) workouts with your self-hosted Endurain instance. It generates standardized binary FIT files including full telemetry (GPS, Heart Rate, Cadence, and Altitude) and precise lap statistics.

## Prerequisites

* Python 3.x
* A Zepp account
* A running Endurain instance

## Installation

1. Create a virtual environment:
```sh
python3 -m venv venv
```

2. Activate the virtual environment:
```sh
# On Linux/macOS
source venv/bin/activate

```

3. Install the required dependencies:
```sh
pip install -r requirements.txt

```

## Configuration

Create and edit a `.env` file in the root folder using `.env.example` as a template. You will need the following variables:

```env
ZEPP_EMAIL=your_zepp_email@example.com
ZEPP_PASSWORD=your_zepp_password
ENDURAIN_URL=http://your-endurain-instance:8080
ENDURAIN_USER=your_endurain_username
ENDURAIN_PASS=your_endurain_password

```

## Manual Usage

To test the synchronization process manually, run:

```sh
python zepp-endurain.py

```

*(Successfully synced tracks will be saved in `synced_tracks.txt` to prevent duplicate uploads).*

## Automation

Create a new cron entry to execute the sync script every hour. Replace `/path/to/your/folder` with the actual absolute path to your project directory:

```cron
0 * * * * cd /path/to/your/folder && /path/to/your/folder/venv/bin/python zepp-endurain.py >> sync.log 2>&1

```

```
