from datetime import datetime
import yaml

version = \
    """# T-Pot Service Builder v0.1
    
    This script is intended as a kickstarter for users who want to build a customzized docker-compose.yml for use with T-Pot.
    
    T-Pot Service Builder will ask you for all the docker services you wish to include in your docker-compose configuration file.
    The configuration file will be checked for conflicting ports as some of the honeypots are meant to work on certain ports.
    You have to manually resolve the port conflicts or re-run the script and exclude the conflicting services / honeypots.
    
    Review the resulting configuration and adjust the port settings to your needs by (un)commenting the corresponding lines in the config.
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
        response = input(f"Include {service_name}? (y/n): ").strip().lower()
        if response in ['y', 'n']:
            return response == 'y'
        else:
            print("Please enter 'y' for yes or 'n' for no.")


def check_port_conflicts(selected_services):
    all_ports = []
    for config in selected_services.values():
        ports = config.get('ports', [])
        for port in ports:
            # Split the port mapping and take only the host port part
            parts = port.split(':')
            if len(parts) == 3:
                # Format: host_ip:host_port:container_port
                host_port = parts[1]
            elif len(parts) == 2:
                # Format: host_port:container_port (or host_ip:host_port for default container_port)
                host_port = parts[0] if parts[1].isdigit() else parts[1]
            else:
                # Single value, treated as host_port
                host_port = parts[0]

            # Check for port conflict
            if host_port in all_ports:
                print_color(f"Port conflict detected: {host_port}", "red")
                return True
            all_ports.append(host_port)
    return False


def print_color(text, color):
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "end": "\033[0m",
    }
    print(f"{colors[color]}{text}{colors['end']}")


def enforce_dependencies(selected_services, services):
    # If snare or any tanner services are selected, ensure all are enabled
    tanner_services = {'snare', 'tanner', 'tanner_redis', 'tanner_phpox', 'tanner_api'}
    if tanner_services.intersection(selected_services):
        for service in tanner_services:
            selected_services[service] = services[service]

    # If kibana is enabled, also enable elasticsearch
    if 'kibana' in selected_services:
        selected_services['elasticsearch'] = services['elasticsearch']

    # If any map services are detected, enable logstash, elasticsearch, nginx, and all map services
    map_services = {'map_web', 'map_redis', 'map_data'}
    if map_services.intersection(selected_services):
        for service in map_services.union({'elasticsearch', 'nginx'}):
            selected_services[service] = services[service]

    # honeytrap and glutton cannot be active at the same time, always vote in favor of honeytrap
    if 'honeytrap' in selected_services and 'glutton' in selected_services:
        # Remove glutton and notify
        del selected_services['glutton']
        print_color(
            "Honeytrap and Glutton cannot be active at the same time. Glutton has been removed from your configuration.",
            "red")


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
        'version': '3.9',
        'networks': networks,
        'services': selected_services,
    }

    current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(service_filename, 'w') as file:
        file.write(header.format(current_date=current_date))
        yaml.dump(output_config, file, default_flow_style=False, sort_keys=False, indent=2)

    if check_port_conflicts(selected_services):
        print_color(f"Adjust the conflicting ports in the {service_filename} or re-run the script and select services that do not occupy the same port(s).",
            "red")
    else:
        print_color(f"Custom {service_filename} has been generated without port conflicts.", "green")
    print(f"Copy {service_filename} to tpotce/ and test with: docker compose -f {service_filename} up")
    print(f"If everything works, exit with CTRL-C and replace docker-compose.yml with the new config.")


if __name__ == "__main__":
    print(version)
    main()
