terraform {
  required_version = ">= 0.13"
  required_providers {
    opentelekomcloud = {
      source = "terraform-providers/opentelekomcloud"
    }
    random = {
      source = "hashicorp/random"
    }
  }
}
