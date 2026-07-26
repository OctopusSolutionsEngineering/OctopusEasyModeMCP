resource "octopusdeploy_variable" "database_name" {
  owner_id     = octopusdeploy_project.test_project.id
  value        = ""
  name         = "DatabaseName"
  type         = "String"
  description  = "The name of the database to back up"
  is_sensitive = false

  prompt {
    description = "The name of the database to back up"
    label       = "Database Name"
    is_required = true

    display_settings {
      control_type = "SingleLineText"
    }
  }
}

resource "octopusdeploy_variable" "notify_on_completion" {
  owner_id     = octopusdeploy_project.test_project.id
  value        = "false"
  name         = "NotifyOnCompletion"
  type         = "String"
  description  = "Whether to send a notification on completion"
  is_sensitive = false

  prompt {
    description = "Whether to send a notification on completion"
    label       = "Notify On Completion"
    is_required = false

    display_settings {
      control_type = "SingleLineText"
    }
  }
}
