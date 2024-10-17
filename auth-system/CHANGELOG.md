# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

On 2024-10-17, we started this module by copying Sparrow's authentication code
at commit `ff0284620462fcaff127069ee00cef91b6412fa5` (2024-09-19). We will
begin by excising Sparrow-specific code and replacing it with a more
generalized authentication system.

Then we will start integrating Macrostrat's ORCID-based system.

- Add user model code from `sparrow.database`