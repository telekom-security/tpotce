## cloud-init configuration ##
variable "timezone" {
  default = "UTC"
}

variable "linux_password" {
  #default = "LiNuXuSeRPaSs#"
  description = "Set a password for the default user"

  validation {
    condition     = length(var.linux_password) > 0
    error_message = "Please specify a password for the default user."
  }
}

## Security Group ##
variable "secgroup_name" {
  default = "sg-tpot"
}

variable "secgroup_desc" {
  default = "Security Group for T-Pot"
}

## Virtual Private Cloud ##
variable "vpc_name" {
  default = "vpc-tpot"
}

variable "vpc_cidr" {
  default = "192.168.0.0/16"
}

## Subnet ##
variable "subnet_name" {
  default = "subnet-tpot"
}

variable "subnet_cidr" {
  default = "192.168.0.0/24"
}

variable "subnet_gateway_ip" {
  default = "192.168.0.1"
}

## Elastic Cloud Server ##
variable "ecs_prefix" {
  default = "tpot-"
}

variable "ecs_flavor" {
  default = "s3.medium.8"
}

variable "ecs_disk_size" {
  default = "128"
}

variable "availability_zone" {
  default = "eu-de-03"
}

variable "key_pair" {
  #default = ""
  description = "Specify your SSH key pair"

  validation {
    condition     = length(var.key_pair) > 0
    error_message = "Please specify a Key Pair."
  }
}

## Elastic IP ##
variable "eip_size" {
  default = "100"
}

## These will go in the generated tpot.conf file ##
variable "tpot_flavor" {
  default     = "STANDARD"
  description = "Specify your tpot flavor [STANDARD, HIVE, HIVE_SENSOR, INDUSTRIAL, LOG4J, MEDICAL, MINI, SENSOR]"
}

variable "web_user" {
  default     = "webuser"
  description = "Set a username for the web user"
}

variable "web_password" {
  #default = "w3b$ecret"
  description = "Set a password for the web user"

  validation {
    condition     = length(var.web_password) > 0
    error_message = "Please specify a password for the web user."
  }
}
