from datetime import datetime
import yaml

version = \
"""
 ____  [T-Pot]         _            ____        _ _     _
/ ___|  ___ _ ____   _(_) ___ ___  | __ ) _   _(_) | __| | ___ _ __
\___ \ / _ \ '__\ \ / / |/ __/ _ \ |  _ \| | | | | |/ _` |/ _ \ '__|
 ___) |  __/ |   \ V /| | (_|  __/ | |_) | |_| | | | (_| |  __/ |
|____/ \___|_|    \_/ |_|\___\___| |____/ \__,_|_|_|\__,_|\___|_| v0.21
    
# This script is intended for users who want to build a customized docker-compose.yml for T-Pot.
# T-Pot Service Builder will ask for all the docker services to be included in docker-compose.yml.
# The configuration file will be checked for conflicting ports.
# Port conflicts have to be resolved manually or re-running the script and excluding the conflicting services.
# Review the resulting docker-compose-custom.yml and adjust to your needs by (un)commenting the corresponding lines in the config.
"""

header = \
"""# T-Pot: CUSTOM EDITION
# Generated on: {current_date}
"""

config_filename = "tpot_services.yml"
service_filename = "docker-compose-custom.yml"


def load_config(filename):
    try:
        with open(filename, 'r') as file:
            config = yaml.safe_load(file)
    except:
        print_color(f"Error: {filename} not found. Exiting.", "red")
        exit()
    return config


def prompt_service_include(service_name):
    while True:
        try:
            response = input(f"Include {service_name}? (y/n): ").strip().lower()
            if response in ['y', 'n']:
                return response == 'y'
            else:
                print_color("Please enter 'y' for yes or 'n' for no.", "red")
        except KeyboardInterrupt:
            print()
            print_color("Interrupted by user. Exiting.", "red")
            print()
            exit()


def check_port_conflicts(selected_services):
    all_ports = {}
    conflict_ports = []

    for service_name, config in selected_services.items():
        ports = config.get('ports', [])
        for port in ports:
            # Split the port mapping and take only the host port part
            parts = port.split(':')
            host_port = parts[1] if len(parts) == 3 else (parts[0] if parts[1].isdigit() else parts[1])

            # Check for port conflict and associate it with the service name
            if host_port in all_ports:
                conflict_ports.append((service_name, host_port))
                if all_ports[host_port] not in [service for service, _ in conflict_ports]:
                    conflict_ports.append((all_ports[host_port], host_port))
            else:
                all_ports[host_port] = service_name

    if conflict_ports:
        print_color("[WARNING] - Port conflict(s) detected:", "red")
        for service, port in conflict_ports:
            print_color(f"{service}: {port}", "red")
        return True
    return False



def print_color(text, color):
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "blue": "\033[94m",  # Added blue
        "magenta": "\033[95m",  # Added magenta
        "end": "\033[0m",
    }
    print(f"{colors[color]}{text}{colors['end']}")

def enforce_dependencies(selected_services, services):
    # If snare or any tanner services are selected, ensure all are enabled
    tanner_services = {'snare', 'tanner', 'tanner_redis', 'tanner_phpox', 'tanner_api'}
    if tanner_services.intersection(selected_services):
        print_color("[OK] - For Snare / Tanner to work all required services have been added to your configuration.", "green")
        for service in tanner_services:
            selected_services[service] = services[service]

    # If kibana is enabled, also enable elasticsearch
    if 'kibana' in selected_services:
        selected_services['elasticsearch'] = services['elasticsearch']
        print_color("[OK] - Kibana requires Elasticsearch which has been added to your configuration.", "green")

    # If spiderfoot is enabled, also enable nginx
    if 'spiderfoot' in selected_services:
        selected_services['nginx'] = services['nginx']
        print_color("[OK] - Spiderfoot requires Nginx which has been added to your configuration.","green")


    # If any map services are detected, enable logstash, elasticsearch, nginx, and all map services
    map_services = {'map_web', 'map_redis', 'map_data'}
    if map_services.intersection(selected_services):
        print_color("[OK] - For AttackMap to work all required services have been added to your configuration.", "green")
        for service in map_services.union({'elasticsearch', 'nginx'}):
            selected_services[service] = services[service]

    # honeytrap and glutton cannot be active at the same time, always vote in favor of honeytrap
    if 'honeytrap' in selected_services and 'glutton' in selected_services:
        # Remove glutton and notify
        del selected_services['glutton']
        print_color("[OK] - Honeytrap and Glutton cannot be active at the same time. Glutton has been removed from your configuration.","green")


def remove_unused_networks(selected_services, services, networks):
    used_networks = set()
    # Identify networks used by selected services
    for service_name in selected_services:
        service_config = services[service_name]
        if 'networks' in service_config:
            for network in service_config['networks']:
                used_networks.add(network)

    # Remove unused networks
    for network in list(networks):
        if network not in used_networks:
            del networks[network]


def main():
    config = load_config(config_filename)

    # Separate services and networks
    services = config['services']
    networks = config.get('networks', {})
    selected_services = {'tpotinit': services['tpotinit'],
                         'logstash': services['logstash']}  # Always include tpotinit and logstash

    for service_name, service_config in services.items():
        if service_name not in selected_services:  # Skip already included services
            if prompt_service_include(service_name):
                selected_services[service_name] = service_config

    # Enforce dependencies
    enforce_dependencies(selected_services, services)

    # Remove unused networks based on selected services
    remove_unused_networks(selected_services, services, networks)

    output_config = {
        'networks': networks,
        'services': selected_services,
    }

    current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(service_filename, 'w') as file:
        file.write(header.format(current_date=current_date))
        yaml.dump(output_config, file, default_flow_style=False, sort_keys=False, indent=2)

    if check_port_conflicts(selected_services):
        print_color(f"[WARNING] - Adjust the conflicting ports in the {service_filename} or re-run the script and select services that do not occupy the same port(s).",
            "red")
    else:
        print_color(f"[OK] - Custom {service_filename} has been generated without port conflicts.", "green")
    print_color(f"Copy {service_filename} to ~/tpotce and test with: docker compose -f {service_filename} up", "blue")
    print_color(f"If everything works, exit with CTRL-C and replace docker-compose.yml with the new config.", "blue")


if __name__ == "__main__":
    print_color(version, "magenta")
    main()
