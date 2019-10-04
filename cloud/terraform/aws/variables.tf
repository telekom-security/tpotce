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
    "ap-east-1"      = "ami-b7d0abc6"
    "ap-northeast-1" = "ami-01f4f0c9374675b99"
    "ap-northeast-2" = "ami-0855cb0c55370c38c"
    "ap-south-1"     = "ami-00d7d1cbdcb087cf3"
    "ap-southeast-1" = "ami-03779b1b2fbb3a9d4"
    "ap-southeast-2" = "ami-0ce3a7c68c6b1678d"
    "ca-central-1"   = "ami-037099906a22f210f"
    "eu-central-1"   = "ami-0845c3902a6f2af32"
    "eu-north-1"     = "ami-e634bf98"
    "eu-west-1"      = "ami-06a53bf81914447b5"
    "eu-west-2"      = "ami-053d9f0770cd2e34c"
    "eu-west-3"      = "ami-060bf1f444f742af9"
    "me-south-1"     = "ami-04a9a536105c72d30"
    "sa-east-1"      = "ami-0a5fd18ed0b9c7f35"
    "us-east-1"      = "ami-01db78123b2b99496"
    "us-east-2"      = "ami-010ffea14ff17ebf5"
    "us-west-1"      = "ami-0ed1af421f2a3cf40"
    "us-west-2"      = "ami-030a304a76b181155"
  }
}
