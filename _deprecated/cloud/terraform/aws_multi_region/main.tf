module "eu-west-2" {
  source = "./modules/multi-region"
  ec2_vpc_id = "vpc-xxxxxxxx"
  ec2_subnet_id = "subnet-xxxxxxxx"
  ec2_region = "eu-west-2"
  tpot_name = "T-Pot Honeypot"
  
  linux_password = var.linux_password
  web_password = var.web_password
  providers = {
    aws = aws.eu-west-2
  }
}

module "us-west-1" {
  source = "./modules/multi-region"
  ec2_vpc_id = "vpc-xxxxxxxx"
  ec2_subnet_id = "subnet-xxxxxxxx"
  ec2_region = "us-west-1"
  tpot_name = "T-Pot Honeypot"

  linux_password = var.linux_password
  web_password = var.web_password
  providers = {
    aws = aws.us-west-1
  }
}
