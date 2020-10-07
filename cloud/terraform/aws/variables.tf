variable "admin_ip" {
  default     = ["127.0.0.1/32"]
  description = "admin IP addresses in CIDR format"
}

variable "ec2_vpc_id" {
  description = "ID of AWS VPC"
  default     = "vpc-XXX"
}

variable "ec2_subnet_id" {
  description = "ID of AWS VPC subnet"
  default     = "subnet-YYY"
}

variable "ec2_region" {
  description = "AWS region to launch servers"
  default     = "eu-west-1"
}

variable "ec2_ssh_key_name" {
  default = "default"
}

# https://aws.amazon.com/ec2/instance-types/
# t3.large = 2 vCPU, 8 GiB RAM
variable "ec2_instance_type" {
  default = "t3.large"
}

# Refer to https://wiki.debian.org/Cloud/AmazonEC2Image/Buster
variable "ec2_ami" {
  type = map(string)
  default = {
    "af-south-1"     = "ami-0cd567568c63c1c50"
    "ap-east-1"      = "ami-da6c2eab"
    "ap-northeast-1" = "ami-0143c7691e0f7cd73"
    "ap-northeast-2" = "ami-08e248bc8d4c0c2c7"
    "ap-south-1"     = "ami-00c3a0e8f345e299e"
    "ap-southeast-1" = "ami-0782777da8d7d10c4"
    "ap-southeast-2" = "ami-01b0588564524ce82"
    "ca-central-1"   = "ami-0de46d86862b936a0"
    "eu-central-1"   = "ami-01580e1a2caffeb61"
    "eu-north-1"     = "ami-00466bdeb1cc0a297"
    "eu-south-1"     = "ami-0e5461d66f95255c9"
    "eu-west-1"      = "ami-0ec224441e69e034e"
    "eu-west-2"      = "ami-0e02b7cae376541f2"
    "eu-west-3"      = "ami-09de525c1f6538ef8"
    "me-south-1"     = "ami-02465bc955e5fa1d1"
    "sa-east-1"      = "ami-08605b43346ed52e8"
    "us-east-1"      = "ami-0fda9f4b1eaa92881"
    "us-east-2"      = "ami-08f6e7446faea65e0"
    "us-west-1"      = "ami-091f15e9ff781f127"
    "us-west-2"      = "ami-06d8a32aedc6986f5"
  }
}

# cloud-init configuration
variable "timezone" {
  default = "UTC"
}

variable "linux_password" {
  #default = "LiNuXuSeRPaSs#"
  description = "Set a password for the default user"
}

# These will go in the generated tpot.conf file
variable "tpot_flavor" {
  default = "STANDARD"
  description = "Specify your tpot flavor [STANDARD, SENSOR, INDUSTRIAL, COLLECTOR, NEXTGEN]"
}

variable "web_user" {
  default = "webuser"
  description = "Set a username for the web user"
}

variable "web_password" {
  #default = "w3b$ecret"
  description = "Set a password for the web user"
}
