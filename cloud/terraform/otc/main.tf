data "opentelekomcloud_images_image_v2" "debian" {
  name = "Standard_Debian_10_latest"
}

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

resource "opentelekomcloud_vpc_v1" "vpc_1" {
  name = var.vpc_name
  cidr = var.vpc_cidr
}

resource "opentelekomcloud_vpc_subnet_v1" "subnet_1" {
  name   = var.subnet_name
  cidr   = var.subnet_cidr
  vpc_id = opentelekomcloud_vpc_v1.vpc_1.id

  gateway_ip = var.subnet_gateway_ip
  dns_list   = ["100.125.4.25", "100.125.129.199"]
}

resource "random_id" "tpot" {
  byte_length = 6
  prefix      = var.ecs_prefix
}

resource "opentelekomcloud_ecs_instance_v1" "ecs_1" {
  name     = random_id.tpot.b64_url
  image_id = data.opentelekomcloud_images_image_v2.debian.id
  flavor   = var.ecs_flavor
  vpc_id   = opentelekomcloud_vpc_v1.vpc_1.id

  nics {
    network_id = opentelekomcloud_vpc_subnet_v1.subnet_1.id
  }

  system_disk_size  = var.ecs_disk_size
  system_disk_type  = "SAS"
  security_groups   = [opentelekomcloud_networking_secgroup_v2.secgroup_1.id]
  availability_zone = var.availability_zone
  key_name          = var.key_pair
  user_data         = templatefile("../cloud-init.yaml", { timezone = var.timezone, password = var.linux_password, tpot_flavor = var.tpot_flavor, web_user = var.web_user, web_password = var.web_password })
}

resource "opentelekomcloud_vpc_eip_v1" "eip_1" {
  publicip {
    type = "5_bgp"
  }
  bandwidth {
    name       = "bandwidth-${random_id.tpot.b64_url}"
    size       = var.eip_size
    share_type = "PER"
  }
}

resource "opentelekomcloud_compute_floatingip_associate_v2" "fip_1" {
  floating_ip = opentelekomcloud_vpc_eip_v1.eip_1.publicip.0.ip_address
  instance_id = opentelekomcloud_ecs_instance_v1.ecs_1.id
}
