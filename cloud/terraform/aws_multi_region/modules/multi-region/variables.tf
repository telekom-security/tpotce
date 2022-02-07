variable "admin_ip" {
  default     = ["152.37.108.93/32"]
  description = "admin IP addresses in CIDR format"
}

variable "ec2_ssh_key_name" {
  default = "default"
}

# https://aws.amazon.com/ec2/instance-types/
variable "ec2_instance_type" {
  default = "t3.xlarge"
}

# Refer to https://wiki.debian.org/Cloud/AmazonEC2Image/Buster
variable "ec2_ami" {
  type = map(string)
  default = {
    "af-south-1"     = "ami-0272d4f5fb1b98a0d"
    "ap-east-1"      = "ami-00d242e2f23abf6d2"
    "ap-northeast-1" = "ami-001c6b4d627e8be53"
    "ap-northeast-2" = "ami-0d841ed4bf80e764c"
    "ap-northeast-3" = "ami-01b0a01d770321320"
    "ap-south-1"     = "ami-04ba7e5bd7c6f6929"
    "ap-southeast-1" = "ami-0dca3eabb09c32ae2"
    "ap-southeast-2" = "ami-03ff8684dc585ddae"
    "ca-central-1"   = "ami-08af22d7c0382fd83"
    "eu-central-1"   = "ami-0f41e297b3c53fab8"
    "eu-north-1"     = "ami-0bbc6a00971c77d6d"
    "eu-south-1"     = "ami-03ff8684dc585ddae"
    "eu-west-1"      = "ami-080684ad73d431a05"
    "eu-west-2"      = "ami-04b259723891dfc53"
    "eu-west-3"      = "ami-00662eead74f66895"
    "me-south-1"     = "ami-021a6c6047091ab5b"
    "sa-east-1"      = "ami-0aac091cce68a049c"
    "us-east-1"      = "ami-05ad4ed7f9c48178b"
    "us-east-2"      = "ami-07640f3f27c0ad3d3"
    "us-west-1"      = "ami-0c053f1d5f22eb09f"
    "us-west-2"      = "ami-090cd3aed687b1ee1"
  }
}

## cloud-init configuration ##
variable "timezone" {
  default = "UTC"
}

## These will go in the generated tpot.conf file ##
variable "tpot_flavor" {
  default     = "STANDARD"
  description = "Specify your tpot flavor [STANDARD, SENSOR, INDUSTRIAL, COLLECTOR, NEXTGEN, MEDICAL]"
}

variable "web_user" {
  default     = "webuser"
  description = "Set a username for the web user"
}
