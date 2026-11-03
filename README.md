# PrepPi

Household emergency preparedness and stock management for Raspberry Pi.

## Features

- Household stock and expiry-date tracking.
- Food endurance calculations and preparedness recommendations.
- Emergency equipment management.
- Individual cat and dog profiles with 14-day pet food requirements.
- Excel stock imports and inventory reports.
- Optional email stock updates, expiry alerts and monthly reports.
- Password-protected dashboard.

## Installation

On a Raspberry Pi running Raspberry Pi OS:

    sudo ./install.sh

Follow the installation prompts.

Once installed, open a browser on your local network and visit:

    http://<raspberry-pi-address>:5000

Create your household password and complete the initial setup.

## Data storage

- Application: `/opt/preppi`
- Household data: `/var/lib/preppi`
- Configuration: `/etc/preppi`
- Backups: `/var/backups/preppi`

## Security

PrepPi is designed for trusted local network use. It currently uses HTTP rather than HTTPS. Do not expose it directly to the public internet or forward its web port through your router.

Use a strong household password and maintain regular backups.

## Testing

Run the automated test suite with:

    .venv/bin/python -m unittest discover -s tests -v

## Version

0.1.5
