terraform {
  required_version = ">= 0.13"
  required_providers {
    opentelekomcloud = {
      source = "opentelekomcloud/opentelekomcloud"
      version = "1.22.5"
    }
    random = {
      source = "hashicorp/random"
      version = "~> 3.0.1"
    }
  }
}
