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

# Refer to https://wiki.debian.org/Cloud/AmazonEC2Image/Bullseye
variable "ec2_ami" {
  type = map(string)
  default = {
    "af-south-1"     = "ami-0c372f041acae6d49"
    "ap-east-1"      = "ami-079b8d011d4655385"
    "ap-northeast-1" = "ami-08dbbf1c0485a4aa8"
    "ap-northeast-2" = "ami-0269fe7d013b8e2dd"
    "ap-northeast-3" = "ami-0848d1e5fb6e3e3da"
    "ap-south-1"     = "ami-020d429f17c9f1d0a"
    "ap-southeast-1" = "ami-09625a221230d9fe6"
    "ap-southeast-2" = "ami-03cbc6cddb06af2c2"
    "ca-central-1"   = "ami-09125623b02302014"
    "eu-central-1"   = "ami-00c36c60f07e21791"
    "eu-north-1"     = "ami-052bea934e2d9dbfe"
    "eu-south-1"     = "ami-04e2bb16d37324719"
    "eu-west-1"      = "ami-0f87948fe2cf1b2a4"
    "eu-west-2"      = "ami-02ed1bc837487d535"
    "eu-west-3"      = "ami-080efd2add7e29430"
    "me-south-1"     = "ami-0dbde382c834c4a72"
    "sa-east-1"      = "ami-0a0792814cb068077"
    "us-east-1"      = "ami-05dd1b6e7ef6f8378"
    "us-east-2"      = "ami-04dd0542609808c50"
    "us-west-1"      = "ami-07af5f877b3db9f73"
    "us-west-2"      = "ami-0d0d8694ba492c02b"
  }
}

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
