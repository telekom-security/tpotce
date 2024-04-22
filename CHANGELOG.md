# Release Notes / Changelog
T-Pot 24.04.0 marks probably the largest change in the history of the project. While most of the changes have been made to the underlying platform some changes will be standing out in particular - a T-Pot ISO image will no longer be provided with the benefit that T-Pot will now run on multiple Linux distributions (Alma Linux, Debian, Fedora, OpenSuse, Raspbian, Rocky Linux, Ubuntu), Raspberry Pi (optimized) and macOS / Windows (limited).

## New Features
* **Distributed** Installation is now using NGINX reverse proxy instead of SSH to transmit **HIVE_SENSOR** logs to **HIVE**
* **`deploy.sh`**, will make the deployment of sensor much easier and will automatically take care of the configuration. You only have to install the T-Pot sensor.  
* **T-Pot Init** is the foundation for running T-Pot on multiple Linux distributions and will also ensure to restart containers with failed healthchecks using **autoheal**
* **T-Pot Installer** is now mostly Ansible based providing a universal playbook for the most common Linux distributions 
* **T-Pot Uninstaller** allows to uninstall T-Pot, while not recommended for general usage, this comes in handy for testing purposes
* **T-Pot Customizer (`compose/customizer.py`)** is here to assist you in the creation of a customized `docker-compose.yml`
* **T-Pot Landing Page** has been redesigned and simplified
![T-Pot-WebUI](doc/tpotwebui.png)
* **Kibana Dashboards, Objects** fully refreshed in favor of Lens based objects
![Dashbaord](doc/kibana_a.png)
* **Wordpot** is added as new addition to the available honeypots within T-Pot and will run on `tcp/8080` by default.
* **Raspberry Pi** is now supported using a dedicated `mobile.yml` (why this is called mobile will be revealed soon!)
* **GeoIP Attack Map** is now aware of connects / disconnects and thus eliminating required reloads
* **Docker**, where possible, will now be installed directly from the Docker repositories to avoid any incompatibilities
* **`.env`** now provides a single configuration file for the T-Pot related settings
* **`genuser.sh`** can now be used to add new users to the T-Pot Landing Page as part of the T-Pot configuration file (`.env`)

## Updates
* **Honeypots** and **tools** were updated to their latest pushed code and / or releases
* Where possible Docker Images will now use Alpine 3.19
* Updates will be provided continuously through Docker Images updates 

## Breaking Changes
* There is no option to migrate a previous installation to T-Pot 24.04.0, you can try to transfer the old `data` folder to the new T-Pot installation, but a working environment depends on too many other factors outside of our control and a new installation is simply faster.
* Most of the support scripts were moved into the **T-Pot Init** image and are no longer available directly on the host.
* Cockpit is no longer available as part of T-Pot itself. However, where supported, you can simply install the `cockpit` package.

# Thanks & Credits
* @sp3t3rs, @trixam, for their backend and ews support!
* @shark4ce for taking the time to test, debug and offer a solution #1472.

... and many others from the T-Pot community by opening valued issues and discussions, suggesting ideas and thus helping to improve T-Pot!