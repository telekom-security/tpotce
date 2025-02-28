## macOS & Windows
Sometimes it is just nice if you can spin up a T-Pot instance on macOS or Windows, i.e. for development, testing or just the fun of it. As Docker Desktop is rather limited not all honeypot types or T-Pot features are supported. Also remember, by default the macOS and Windows firewall are blocking access from remote, so testing is limited to the host. For production it is recommended to run T-Pot on [Linux](#choose-your-distro).<br>
To get things up and running just follow these steps:
1. Install Docker Desktop for [macOS](https://docs.docker.com/desktop/install/mac-install/) or [Windows](https://docs.docker.com/desktop/install/windows-install/).
2. Clone the GitHub repository: `git clone https://github.com/telekom-security/tpotce` (in Windows make sure the code is checked out with `LF` instead of `CRLF`!)
3. Go to: `cd ~/tpotce`
4. Copy `cp compose/mac_win.yml ./docker-compose.yml`
5. Create a `WEB_USER` by running `~/tpotce/genuser.sh` (macOS) or `~/tpotce/genuserwin.ps1` (Windows)
6. Adjust the `.env` file by changing `TPOT_OSTYPE=linux` to either `mac` or `win`:
   ```
   # OSType (linux, mac, win)
   #  Most docker features are available on linux
   TPOT_OSTYPE=mac
   ```
7. You have to ensure on your own there are no port conflicts keeping T-Pot from starting up.
8. Start T-Pot: `docker compose up` or `docker compose up -d` if you want T-Pot to run in the background.
9. Stop T-Pot: `CTRL-C` (it if was running in the foreground) and / or `docker compose down -v` to stop T-Pot entirely.

## Uninstall T-Pot
Uninstallation of T-Pot is only available on the [supported Linux distros](#choose-your-distro).<br>
To uninstall T-Pot run `~/tpotce/uninstall.sh` and follow the uninstaller instructions, you will have to enter your password at least once.<br>
Once the uninstall is finished reboot the machine `sudo reboot`
<br><br>
