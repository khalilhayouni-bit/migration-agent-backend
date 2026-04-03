def validate_results(results: list[dict]) -> tuple[list[dict], list[dict]]:
    valid = []
    flagged = []

    for result in results:
        issues = []

        if not result.get("translated_code"):
            issues.append("Missing translated code")

        if result.get("translated_code", "").startswith("// Translation failed"):
            issues.append("Translation failed during AI generation")

        if not result.get("component_id"):
            issues.append("Missing component ID")

        if issues:
            result["validation_issues"] = issues
            flagged.append(result)
        else:
            result["validation_issues"] = []
            valid.append(result)

    return valid, flagged