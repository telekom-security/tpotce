# cloud-init configuration
variable "timezone" {
  default = "UTC"
}

variable "linux_password" {
  #default = "LiNuXuSeRPaSs#"
  description = "Set a password for the default user"
}

# Cloud resources name configuration
variable "secgroup_name" {
  default = "tpot-secgroup"
}

variable "secgroup_desc" {
  default = "T-Pot Security Group"
}

variable "network_name" {
  default = "tpot-network"
}

variable "subnet_name" {
  default = "tpot-subnet"
}

variable "router_name" {
  default = "tpot-router"
}

variable "ecs_prefix" {
  default = "tpot-"
}

# ECS configuration
variable "availability_zone" {
  default = "eu-de-03"
  description = "Select an availability zone"
}

variable "flavor" {
  default = "s3.medium.8"
  description = "Select a compute flavor"
}

variable "key_pair" {
  #default = ""
  description = "Specify your SSH key pair"
}

variable "volume_size" {
  default = "128"
  description = "Set the volume size"
}

# These will go in the generated tpot.conf file
variable "tpot_flavor" {
  default = "STANDARD"
  description = "Specify your tpot flavor [STANDARD, SENSOR, INDUSTRIAL, COLLECTOR, NEXTGEN, MEDICAL]"
}

variable "web_user" {
  default = "webuser"
  description = "Set a username for the web user"
}

variable "web_password" {
  #default = "w3b$ecret"
  description = "Set a password for the web user"
}
