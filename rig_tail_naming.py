"""
rig_tail_naming.py
author: Daisy Jane @gnitemouse

Naming templates and helpers for Rig Tail.
Utilities for node naming, pattern matching, and name extraction.

Functions:
    fstr: Evaluate fstring template for naming convention
    get_rigname: Extract rigname from node using template
    compile_template_to_regex: Compile naming template to regex
    parse_placeholder: Parse placeholder content from template
    get_index_from_name: Extract numerical index from node name
    titlecase: Convert text to title case
    name_contains_rigname_terms: Check if name matches rigname terms
    rename_shapes: Rename shape nodes to match transform
"""

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import re

logger = logger_setup(__name__)


def fstr(rigname, template, TYPE='', NN='', nn='', TAG=''):
    """
    Evaluate fstring template for naming convention.

    Arguments:
        rigname (str): Name of rig component
        template (str): Naming template with placeholders
        TYPE (str): Type prefix (FK, IK, BN, FX)
        NN (int/str): Primary index number
        nn (int/str): Secondary index number
        TAG (str): Additional tag suffix

    Return:
        str: Evaluated name following naming convention
    """
    DFORMAT = rt_cst.DFORMAT
    GRP = rt_cst.GRP
    CTRL = rt_cst.CTRL
    JNT = rt_cst.JNT
    SDK = rt_cst.SDK
    CRV = rt_cst.CRV
    CSR = rt_cst.CSR
    HDL = rt_cst.HDL
    EFF = rt_cst.EFF
    VIS = rt_cst.VIS
    COND = rt_cst.COND
    CST = rt_cst.CST

    if '{ROOT}' in template:
        template = template.replace('{ROOT}', rt_cst.ROOT)
    if NN != '' and NN != 'ee':
        NN = f"{DFORMAT.format(int(NN))}"
    if nn != '' and nn != 'ee':
        nn = f"{DFORMAT.format(int(nn))}"
    name_eval = eval(f"f'''{template}'''")
    # Clean double / leading / trailing underscores
    parts = name_eval.split('_')
    name = '_'.join([p for p in parts if p])
    return name


def get_rigname(node, template):
    """
    Get rigname from node, provided a naming template.
    Node name must follow the naming convention from template.

    Example:
        jnt = 'FK_L_tail3_00_jnt'
        template = '{TYPE}{rigname}_{NN:02d}{JNT}'
        get_rigname(jnt, template) = 'L_tail3'

    Arguments:
        node (str): Node name to extract rigname from
        template (str): Naming template with {rigname} placeholder

    Return:
        str or None: Extracted rigname, or None if not found
    """
    if '{rigname}' not in template:
        logger.warning('Invalid naming template. Ensure template includes {rigname}.')
        return None

    regex = compile_template_to_regex(template)
    m = regex.match(node)
    return m.group('rigname') if m else None


def compile_template_to_regex(template):
    """
    Compile naming template to regex pattern for matching.

    Arguments:
        template (str): Naming template with placeholders

    Return:
        re.Pattern: Compiled regex pattern
    """
    # Break into tokens: {placeholder} or literal text
    tokens = re.findall(r'\{[^}]+\}|[^{}]+', template)
    parts = []

    for tok in tokens:
        if tok.startswith('{') and tok.endswith('}'):
            raw = tok[1:-1]
            leading, name, trailing = parse_placeholder(raw)

            # Insert leading literal
            if leading:
                parts.append(re.escape(leading))

            # Capture rigname, wildcard everything else
            if name == 'rigname':
                parts.append(r'(?P<rigname>[^_]+)')
            else:
                parts.append(r'.+?')

            # Insert trailing literal
            if trailing:
                parts.append(re.escape(trailing))

        else:
            # Literal text outside placeholders
            parts.append(re.escape(tok))

    pattern = ''.join(parts)
    return re.compile(pattern + r'\Z')


def parse_placeholder(raw):
    """
    Parse placeholder content from template.

    Arguments:
        raw (str): Content inside { ... }

    Return:
        tuple: (leading_literal, name, trailing_literal)
    """
    # Strip formatting, e.g. NN_:02d → NN_
    raw_no_fmt = raw.split(':')[0]

    # Leading literal = non-alphanumeric prefix
    m = re.match(r'(\W+)([\w\W]+)', raw_no_fmt)
    if m:
        leading, rest = m.groups()
    else:
        leading = ''
        rest = raw_no_fmt

    # Trailing literal = non-alphanumeric suffix
    m = re.match(r'([A-Za-z0-9]+)(\W+)$', rest)
    if m:
        name, trailing = m.groups()
    else:
        name = rest
        trailing = ''

    return leading, name, trailing


def get_index_from_name(node, first=False, underscore=True):
    """
    Extract a numerical index (int) from a node name.

    Arguments:
        node (str): Node name
        first (bool): If True, return first valid index; else return last
        underscore (bool): If True, index must be preceded by whitespace/underscore/dash

    Return:
        int or str or None: Index number, 'ee' for end effector, or None if not found

    Examples:
        'R_tail_01_jnt' -> 1 (underscore=True)
        '01_tail_jnt' -> 1 (start of string)
        'Rtail01_jnt' -> None (underscore=True, preceded by 'l')
    """
    if underscore:
        pattern = (
            r'(?:(?<=\s)(?:\d+|ee)|'
            r'(?<=_)(?:\d+|ee)|'
            r'(?<=-)(?:\d+|ee)|'
            r'^(?:\d+|ee))'
        )
    else:
        pattern = r'(?:\d+|ee)'

    if first:
        m = re.search(pattern, node)
        if not m:
            return None
        token = m.group()
        return token if token == 'ee' else int(token)

    matches = re.findall(pattern, node)
    if not matches:
        return None
    token = matches[-1]
    return token if token == 'ee' else int(token)


def titlecase(text, underscore=True):
    """
    Convert text to title case.

    Arguments:
        text (str): Text to convert
        underscore (bool): If True, split on underscores; else split on whitespace

    Return:
        str: Title-cased text with spaces
    """
    if underscore:
        words = text.split('_')
    else:
        words = text.split()
    return ' '.join(word.title() for word in words)


def name_contains_rigname_terms(rigname, name, terms=r'mesh|geo|geometry'):
    """
    Check if name contains rigname and specified terms.

    Arguments:
        rigname (str): Rigname to search for
        name (str): Full name to check
        terms (str): Regex pattern of terms to match

    Return:
        bool: True if name matches pattern
    """
    # Valid whitespace: underscore, dash, space
    sep = r'[_\-\s]*'
    # Regex Pattern: one part must match rigname exactly,
    # other part matches terms (case insensitive), and
    # both can appear in any order
    pattern = (
        rf'(?i)(?=.*\b{re.escape(rigname)}{sep}({terms})\b)'
        rf'|(?=.*\b({terms}){sep}{re.escape(rigname)}\b)'
    )
    return re.search(pattern, name) is not None


def rename_shapes(node, typ='ctrl', prefix='', suffix='Shape'):
    """
    Rename shape nodes to match their transform.

    Arguments:
        node (str): Transform node name
        typ (str): Type suffix in name (e.g., 'ctrl', 'crv')
        prefix (str): Optional prefix for shape name
        suffix (str): Suffix for shape name (default 'Shape')
    """
    shapes = cmds.listRelatives(node, s=True, f=True) or []
    name = node.lstrip('|').rsplit(f"_{typ}", 1)[0]
    i = 0
    for shape in shapes:
        if 'Orig' in shape:
            cmds.rename(shape, f"{prefix}{name}_{typ}{suffix}Orig")
        else:
            if i > 0:
                cmds.rename(shapes[i], f"{prefix}{name}_{i:02}_{typ}{suffix}")
            else:
                cmds.rename(shapes[0], f"{prefix}{name}_{typ}{suffix}")
            i += 1
