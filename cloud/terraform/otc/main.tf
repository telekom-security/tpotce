resource "opentelekomcloud_networking_secgroup_v2" "secgroup_1" {
  name        = var.secgroup_name
  description = var.secgroup_desc
}

resource "opentelekomcloud_networking_secgroup_rule_v2" "secgroup_rule_1" {
  direction         = "ingress"
  ethertype         = "IPv4"
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = opentelekomcloud_networking_secgroup_v2.secgroup_1.id
}

resource "opentelekomcloud_networking_network_v2" "network_1" {
  name = var.network_name
}

resource "opentelekomcloud_networking_subnet_v2" "subnet_1" {
  name            = var.subnet_name
  network_id      = opentelekomcloud_networking_network_v2.network_1.id
  cidr            = "192.168.0.0/24"
  dns_nameservers = ["1.1.1.1", "8.8.8.8"]
}

resource "opentelekomcloud_networking_router_v2" "router_1" {
  name = var.router_name
}

resource "opentelekomcloud_networking_router_interface_v2" "router_interface_1" {
  router_id = opentelekomcloud_networking_router_v2.router_1.id
  subnet_id = opentelekomcloud_networking_subnet_v2.subnet_1.id
}

resource "random_id" "tpot" {
  byte_length = 6
  prefix      = var.ecs_prefix
}

resource "opentelekomcloud_compute_instance_v2" "ecs_1" {
  availability_zone = var.availability_zone
  name              = random_id.tpot.b64
  flavor_name       = var.flavor
  key_pair          = var.key_pair
  security_groups   = [opentelekomcloud_networking_secgroup_v2.secgroup_1.name]
  user_data         = templatefile("../cloud-init.yaml", {timezone = var.timezone, password = var.linux_password, tpot_flavor = var.tpot_flavor, web_user = var.web_user, web_password = var.web_password})

  network {
    name = opentelekomcloud_networking_network_v2.network_1.name
  }

  block_device {
    uuid                  = var.image_id
    source_type           = "image"
    volume_size           = var.volume_size
    destination_type      = "volume"
    delete_on_termination = "true"
  }

  depends_on = [opentelekomcloud_networking_router_interface_v2.router_interface_1]
}

resource "opentelekomcloud_networking_floatingip_v2" "floatip_1" {
}

resource "opentelekomcloud_compute_floatingip_associate_v2" "fip_2" {
  floating_ip = opentelekomcloud_networking_floatingip_v2.floatip_1.address
  instance_id = opentelekomcloud_compute_instance_v2.ecs_1.id
}
