# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

On 2024-10-17, we started this module by copying Sparrow's authentication code
at commit `ff0284620462fcaff127069ee00cef91b6412fa5` (2024-09-19). We will
begin by excising Sparrow-specific code and replacing it with a more
generalized authentication system.

Then we will start integrating Macrostrat's ORCID-based system.

- Remove user model code from `sparrow.database`
- Get all tests to pass by mocking database

Copied the Macrostrat v2 security model from Macrostrat-xdd repository [commit
`79330fa`](https://github.com/UW-Macrostrat/macrostrat-xdd/commit/79d30fa3fe3be62ca80cedc69752d3825fabadbf).
