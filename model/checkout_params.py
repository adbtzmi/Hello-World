"""
model/checkout_params.py
========================
Pydantic models for checkout parameter validation.
Inspired by CAT's ProfileWriter (ProfileDump/main.py:102-224).
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Dict, Tuple
import os
import re


class TestCaseConfig(BaseModel):
    """Single test case configuration."""
    type: str = Field(..., pattern=r"^(passing|force_fail)$")
    label: str = Field(default="passing")
    description: str = Field(default="")

    @field_validator('label', mode='before')
    @classmethod
    def clean_label(cls, v):
        return v.strip() if v else "passing"


class AttrOverwrite(BaseModel):
    """Single attribute override entry.

    Supports both BENTO legacy sections (MAM, MCTO, etc.) and
    CAT/SLATE profile sections (RecipeFile, MaterialInfo, etc.).
    """
    section: str = Field(..., description="Profile section name")
    name: str = Field(..., description="Attribute name")
    value: str = Field(..., description="Attribute value")

    @field_validator('section', mode='before')
    @classmethod
    def validate_section(cls, v):
        v = v.strip() if v else ""
        if not v:
            raise ValueError("Section name cannot be empty")
        return v

    @field_validator('name', 'value', mode='before')
    @classmethod
    def strip_fields(cls, v):
        return v.strip() if v else ""

    @model_validator(mode='after')
    def validate_triplet(self):
        """Ensure section, attribute, and value are all non-empty."""
        if not self.section.strip():
            raise ValueError("Section name cannot be empty")
        if not self.name.strip():
            raise ValueError("Attribute name cannot be empty")
        return self


class ProfileRowParams(BaseModel):
    """Parameters for a single profile row (MID)."""
    mid: str = Field(..., min_length=1)
    lot: str = Field(default="")
    cfgpn: str = Field(default="")
    mcto: str = Field(default="")
    step: str = Field(default="ABIT")
    form_factor: str = Field(default="")
    tester: str = Field(default="")
    primitive: str = Field(default="")
    dut: str = Field(default="")
    attr_overwrites: List[AttrOverwrite] = Field(default_factory=list)

    @field_validator('step', mode='before')
    @classmethod
    def validate_step(cls, v):
        v = v.strip().upper() if v else "ABIT"
        valid = {"ABIT", "SFN2", "SCHP", "CNFG", "MFGV"}
        if v and v not in valid:
            raise ValueError(f"Step must be one of {valid}, got '{v}'")
        return v

    @field_validator('mid', mode='before')
    @classmethod
    def clean_mid(cls, v):
        return v.strip() if v else ""


class CheckoutParams(BaseModel):
    """
    Validated checkout parameters.

    Replaces the raw dict from checkout_tab._collect_params().
    Modeled after CAT's ProfileWriter (Pydantic BaseModel with validators).
    """
    jira_key: str = Field(default="TSESSD-XXXX")
    tgz_path: str = Field(..., min_length=1, description="Path to compiled TGZ")
    hot_folder: str = Field(default=r"C:\test_program\playground_queue")
    hostnames: List[str] = Field(..., min_length=1)
    env: str = Field(default="ABIT")
    site: str = Field(default="SINGAPORE")

    # DUT info
    mid: str = Field(default="")
    cfgpn: str = Field(default="")
    fw_ver: str = Field(default="")
    dut_slots: int = Field(default=4, ge=1, le=128)
    lot_prefix: str = Field(default="JAANTJB")

    # Test cases
    test_cases: List[TestCaseConfig] = Field(..., min_length=1)

    # Profile table (for multi-MID mode)
    profile_table: List[ProfileRowParams] = Field(default_factory=list)

    # Detection & timeout
    detect_method: str = Field(default="sidecar")
    timeout_minutes: int = Field(default=30, ge=1, le=480)

    # Notifications
    notify_teams: bool = Field(default=False)
    webhook_url: str = Field(default="")

    # Autostart
    autostart: str = Field(default="True")

    # TempTraveler generation
    generate_tmptravl: bool = Field(default=False, description="Generate TempTraveler .dat file")

    @field_validator('site', mode='before')
    @classmethod
    def validate_site(cls, v):
        """Auto-uppercase site and validate against known list.

        Mirrors CAT's field_validator('site').
        Allows empty string (defaults to SINGAPORE).
        """
        if not v:
            return "SINGAPORE"
        v = v.strip().upper()
        _VALID_SITES = {"SINGAPORE", "PENANG", "BOISE", "XIAN"}
        if v and v not in _VALID_SITES:
            raise ValueError(
                f"Site must be one of {sorted(_VALID_SITES)}, got '{v}'"
            )
        return v

    @field_validator('env', mode='before')
    @classmethod
    def validate_env(cls, v):
        v = v.strip().upper() if v else "ABIT"
        return v

    @field_validator('autostart', mode='before')
    @classmethod
    def validate_autostart(cls, v):
        """Mirrors CAT's field_validator('autostart')."""
        return v.strip().capitalize() if v else "True"

    @field_validator('tgz_path', mode='before')
    @classmethod
    def validate_tgz_path(cls, v):
        v = v.strip() if v else ""
        if not v:
            raise ValueError("TGZ path cannot be empty")
        return v

    @model_validator(mode='after')
    def validate_checkout_params(self):
        """Cross-field validation."""
        if not self.hostnames:
            raise ValueError("At least one tester hostname must be selected")
        if not self.test_cases:
            raise ValueError("At least one test case (PASSING or FORCE FAIL) must be selected")
        return self

    def to_legacy_dict(self) -> dict:
        """Convert to the legacy dict format for backward compatibility with existing code."""
        return {
            "jira_key": self.jira_key,
            "tgz_path": self.tgz_path,
            "hot_folder": self.hot_folder,
            "hostnames": self.hostnames,
            "mid": self.mid,
            "cfgpn": self.cfgpn,
            "fw_ver": self.fw_ver,
            "dut_slots": self.dut_slots,
            "lot_prefix": self.lot_prefix,
            "test_cases": [tc.model_dump() for tc in self.test_cases],
            "profile_table": [pr.model_dump() for pr in self.profile_table],
            "detect_method": self.detect_method,
            "timeout_minutes": self.timeout_minutes,
            "notify_teams": self.notify_teams,
            "webhook_url": self.webhook_url,
            "env": self.env,
            "site": self.site,
            "autostart": self.autostart,
            "generate_tmptravl": self.generate_tmptravl,
        }


def validate_attr_overwrite_string(value: str) -> Tuple[bool, str]:
    """Validate an ATTR_OVERWRITE string format.

    Expected format: semicolon-delimited triplets (section;attr;value repeated).
    Total number of semicolon-separated parts must be divisible by 3.

    Parameters
    ----------
    value : str
        The ATTR_OVERWRITE string to validate.

    Returns
    -------
    tuple of (bool, str)
        (is_valid, error_message). error_message is empty if valid.
    """
    if not value or not value.strip():
        return True, ""  # Empty is valid (no overrides)

    parts = [p.strip() for p in value.split(";") if p.strip()]

    if len(parts) % 3 != 0:
        return False, (
            f"ATTR_OVERWRITE must contain triplets (section;attribute;value). "
            f"Found {len(parts)} parts, which is not divisible by 3."
        )

    # Valid XML section names from SLATE profile schema
    valid_sections = {
        "RecipeFile", "TempTraveler", "MaterialInfo", "TestJobArchive",
        "AutoStart", "DutInfo", "TestProgramInfo", "General",
        # BENTO legacy sections
        "MAM", "MCTO", "CFGPN", "EQUIPMENT", "DRIVE_INFO",
    }

    errors = []
    for i in range(0, len(parts), 3):
        section = parts[i]
        attr_name = parts[i + 1]
        attr_value = parts[i + 2]
        triplet_num = (i // 3) + 1

        if not section:
            errors.append(f"Triplet {triplet_num}: section name is empty")
        elif section not in valid_sections:
            # Warning, not error — allow custom sections
            pass

        if not attr_name:
            errors.append(f"Triplet {triplet_num}: attribute name is empty")

    if errors:
        return False, "; ".join(errors)

    return True, ""


def parse_attr_overwrite_string(attr_str: str) -> List[AttrOverwrite]:
    """
    Parse semicolon-delimited ATTR_OVERWRITE string into validated objects.
    Format: "SECTION;NAME;VALUE;SECTION;NAME;VALUE;..."
    Mirrors CAT's Modify() function (ProfileDump/main.py:278-301).
    """
    if not attr_str or not attr_str.strip():
        return []
    parts = [p.strip() for p in attr_str.split(";") if p.strip()]
    if len(parts) % 3 != 0:
        raise ValueError(
            f"ATTR_OVERWRITE must have groups of 3 (section;name;value). "
            f"Got {len(parts)} parts."
        )
    result = []
    for i in range(0, len(parts), 3):
        result.append(AttrOverwrite(
            section=parts[i],
            name=parts[i + 1],
            value=parts[i + 2],
        ))
    return result
