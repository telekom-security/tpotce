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
    "af-south-1"     = "ami-04090a79eb0bcb6c1"
    "ap-east-1"      = "ami-0327f60df432e2479"
    "ap-northeast-1" = "ami-06bc324209030cbc8"
    "ap-northeast-2" = "ami-02ee842962ae7df95"
    "ap-south-1"     = "ami-0d548fffbb2d54e42"
    "ap-southeast-1" = "ami-0dcf891cda6248f00"
    "ap-southeast-2" = "ami-022578f782d4e5d30"
    "ca-central-1"   = "ami-01444dd84a75e9a82"
    "eu-central-1"   = "ami-097411fa8fbfdffda"
    "eu-north-1"     = "ami-026984326b6456f6a"
    "eu-south-1"     = "ami-07ad114e5df69197e"
    "eu-west-1"      = "ami-0101794b418f8b2a6"
    "eu-west-2"      = "ami-00eac9341e72e638a"
    "eu-west-3"      = "ami-01469c569416f3bd3"
    "me-south-1"     = "ami-0821f357b877b076d"
    "sa-east-1"      = "ami-0c87b2c6219e3d5fd"
    "us-east-1"      = "ami-047f0b13f023f6553"
    "us-east-2"      = "ami-0988470f4e830799f"
    "us-west-1"      = "ami-0be6bacfeb2913ac2"
    "us-west-2"      = "ami-0112d55fbe29acc68"
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
