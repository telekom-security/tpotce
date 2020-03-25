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
    "ap-east-1"      = "ami-f9c58188"
    "ap-northeast-1" = "ami-0fae5501ae428f9d7"
    "ap-northeast-2" = "ami-0522874b039290246"
    "ap-south-1"     = "ami-03b4e18f70aca8973"
    "ap-southeast-1" = "ami-0852293c17f5240b3"
    "ap-southeast-2" = "ami-03ea2db714f1f6acf"
    "ca-central-1"   = "ami-094511e5020cdea18"
    "eu-central-1"   = "ami-0394acab8c5063f6f"
    "eu-north-1"     = "ami-0c82d9a7f5674320a"
    "eu-west-1"      = "ami-006d280940ad4a96c"
    "eu-west-2"      = "ami-08fe9ea08db6f1258"
    "eu-west-3"      = "ami-04563f5eab11f2b87"
    "me-south-1"     = "ami-0492a01b319d1f052"
    "sa-east-1"      = "ami-05e16feea94258a69"
    "us-east-1"      = "ami-04d70e069399af2e9"
    "us-east-2"      = "ami-04100f1cdba76b497"
    "us-west-1"      = "ami-014c78f266c5b7163"
    "us-west-2"      = "ami-023b7a69b9328e1f9"
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
