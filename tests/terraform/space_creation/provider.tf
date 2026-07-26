terraform {
  required_providers {
    octopusdeploy = {
      source = "OctopusDeployLabs/octopusdeploy"
    }
  }
}

provider "octopusdeploy" {
  address = "${trimspace(var.octopus_server)}"
  api_key = "${trimspace(var.octopus_apikey)}"
}
