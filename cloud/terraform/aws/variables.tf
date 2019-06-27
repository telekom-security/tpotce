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

# Refer to https://wiki.debian.org/Cloud/AmazonEC2Image/Stretch
variable "ec2_ami" {
  type = map(string)
  default = {
    "ap-northeast-1" = "ami-09fbcd30452841cb9"
    "ap-northeast-2" = "ami-08363ccce96df1fff"
    "ap-south-1"     = "ami-0dc98cbb0d0e49162"
    "ap-southeast-1" = "ami-0555b1a5444087dd4"
    "ap-southeast-2" = "ami-029c54f988446691a"
    "ca-central-1"   = "ami-04413a263a7d94982"
    "eu-central-1"   = "ami-01fb3b7bab31acac5"
    "eu-north-1"     = "ami-050f04ca573daa1fb"
    "eu-west-1"      = "ami-0968f6a31fc6cffc0"
    "eu-west-2"      = "ami-0faa9c9b5399088fd"
    "eu-west-3"      = "ami-0cd23820af84edc85"
    "sa-east-1"      = "ami-030580e61468e54bd"
    "us-east-1"      = "ami-0357081a1383dc76b"
    "us-east-2"      = "ami-09c10a66337c79669"
    "us-west-1"      = "ami-0adbaf2e0ce044437"
    "us-west-2"      = "ami-05a3ef6744aa96514"
  }
}

