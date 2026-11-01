"""The fields /api/myst/build accepts. roboneuro-neo's MyST build commands may send only these (the server refuses
others with a 422), and its spec/myst_build_commands_spec.rb lists them as ACCEPTED. If this test fails because
the schema changed, update that list in roboneuro-neo as well."""

import schema

ROBONEURO_ACCEPTED = {"id", "repository_url", "commit_hash", "binder_hash", "is_prod", "build_cache", "prod_version",
                      "fresh_execution"}


def test_the_myst_build_fields_match_what_roboneuro_expects():
    assert set(schema.MystBuildSchema().fields) == ROBONEURO_ACCEPTED, (
        "MystBuildSchema changed: update ACCEPTED in roboneuro-neo's spec/myst_build_commands_spec.rb too")
