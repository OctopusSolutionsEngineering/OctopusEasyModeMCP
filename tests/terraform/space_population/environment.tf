resource "octopusdeploy_environment" "development" {
  name                         = "Development"
  description                  = "Development environment"
  allow_dynamic_infrastructure = true
  use_guided_failure           = false
  sort_order                   = 0
}

resource "octopusdeploy_environment" "production" {
  name                         = "Production"
  description                  = "Production environment"
  allow_dynamic_infrastructure = true
  use_guided_failure           = false
  sort_order                   = 1
}
