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
    replace_index_in_name: Rewrite a name's index token, keeping the name
    strip_group_suffix: Strip trailing group label from a name
    titlecase: Convert text to title case
    name_contains_rigname_terms: Check if name matches rigname terms
    rename_shapes: Rename shape nodes to match transform
"""

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import re

logger = logger_setup(__name__)

# Compiled f-string per naming template. fstr is the most-called function
# in the build (every node name, in every phase, goes through it) and
# eval() on a string re-parses and re-compiles the template on every call.
# Compiling a given source string is deterministic, so only the code
# object is cached; the evaluation itself still runs per call and still
# sees the caller's live values. Bounded by the number of templates in
# rig_tail_constants, so it needs no eviction.
_TEMPLATE_CODE = {}

# The index token in a node's own name: digits, or the 'ee' end-joint
# marker. Shared by get_index_from_name and replace_index_in_name so the
# token that is read is exactly the token that gets written.
_INDEX_PATTERN = (
    r'(?:(?<=\s)(?:\d+|ee)|'
    r'(?<=_)(?:\d+|ee)|'
    r'(?<=-)(?:\d+|ee)|'
    r'^(?:\d+|ee))'
)
_INDEX_PATTERN_ANY = r'(?:\d+|ee)'


def _template_code(template):
    """
    Compiled f-string expression for a naming template.

    Arguments:
        template (str): Naming template with placeholders

    Return:
        code: Compiled 'eval' code object for the template
    """
    code = _TEMPLATE_CODE.get(template)
    if code is None:
        code = compile(f"f'''{template}'''", '<rig_tail naming template>',
                       'eval')
        _TEMPLATE_CODE[template] = code
    return code


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
    DFORMAT = rt_constants.DFORMAT
    GRP = rt_constants.GRP
    CTRL = rt_constants.CTRL
    JNT = rt_constants.JNT
    SDK = rt_constants.SDK
    CRV = rt_constants.CRV
    CSR = rt_constants.CSR
    HDL = rt_constants.HDL
    EFF = rt_constants.EFF
    VIS = rt_constants.VIS
    COND = rt_constants.COND
    CST = rt_constants.CST

    if '{ROOT}' in template:
        template = template.replace('{ROOT}', rt_constants.ROOT)
    if NN != '' and NN != 'ee':
        NN = f"{DFORMAT.format(int(NN))}"
    if nn != '' and nn != 'ee':
        nn = f"{DFORMAT.format(int(nn))}"
    # eval on the cached code object, not on the template text: it runs in
    # this frame either way, so the locals above are still what the
    # placeholders resolve against
    name_eval = eval(_template_code(template))
    # Clean double / leading / trailing underscores
    parts = name_eval.split('_')
    name = '_'.join([p for p in parts if p])
    return name


def get_rigname(node, template):
    """
    Get rigname from node, provided a naming template.
    Node name must follow the naming convention from template.

    A DAG path is reduced to its leaf first: the template describes a node's
    OWN name, and the pattern is anchored at both ends, so a full path such
    as '|squid|skeleton|BN_L_tail3_00_jnt' would match nothing and silently
    report no rigname. Callers that carry full paths (joint chains do, so
    duplicate short names stay usable) rely on this.

    Example:
        jnt = 'FK_L_tail3_00_jnt'
        template = '{TYPE}{rigname}_{NN:02d}{JNT}'
        get_rigname(jnt, template) = 'L_tail3'

    Arguments:
        node (str): Node name or DAG path to extract rigname from
        template (str): Naming template with {rigname} placeholder

    Return:
        str or None: Extracted rigname, or None if not found
    """
    if '{rigname}' not in template:
        logger.warning('Invalid naming template. Ensure template includes {rigname}.')
        return None
    if not node:
        return None

    regex = compile_template_to_regex(template)
    m = regex.match(node.split('|')[-1])
    return m.group('rigname') if m else None


def compile_template_to_regex(template):
    """
    Compile naming template to regex pattern for matching.
    Known placeholders (TYPE, NN/nn, type labels such as JNT/GRP/CTRL)
    are resolved to their exact values; only {rigname} is captured.

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

            # Capture rigname; resolve every other placeholder to its
            # exact value so the neighboring tokens anchor the capture:
            # NN/nn are indices (digits or 'ee'), TYPE is one of the
            # joint type labels, and labels like JNT/GRP/CTRL resolve
            # to their rt_constants constants. This keeps multi-token
            # rignames unambiguous without greedy captures:
            # 'BN_C_tail_00_jnt' -> 'C_tail', never 'C' or 'C_tail_00'.
            if name == 'rigname':
                parts.append(r'(?P<rigname>.+?)')
            elif name in ('NN', 'nn'):
                parts.append(r'(?:\d+|ee)')
            elif name == 'TYPE':
                types = [rt_constants.TYPE_BN, rt_constants.TYPE_IK,
                         rt_constants.TYPE_FK, rt_constants.TYPE_FX]
                parts.append('(?:' + '|'.join(re.escape(t) for t in types) + ')')
            else:
                const = getattr(rt_constants, name, None)
                if isinstance(const, str) and const:
                    parts.append(re.escape(const))
                else:
                    # Unknown placeholder (e.g. TAG): wildcard
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

    A DAG path is reduced to its leaf first: the index belongs to the node's
    own name, and reading it off the path would let an ancestor's index
    stand in for a joint that has none of its own.

    Arguments:
        node (str): Node name or DAG path
        first (bool): If True, return first valid index; else return last
        underscore (bool): If True, index must be preceded by whitespace/underscore/dash

    Return:
        int or str or None: Index number, 'ee' for end effector, or None if not found

    Examples:
        'R_tail_01_jnt' -> 1 (underscore=True)
        '01_tail_jnt' -> 1 (start of string)
        'Rtail01_jnt' -> None (underscore=True, preceded by 'l')
    """
    if not node:
        return None
    node = node.split('|')[-1]

    pattern = _INDEX_PATTERN if underscore else _INDEX_PATTERN_ANY

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


def replace_index_in_name(node, index, underscore=True):
    """
    Rewrite a name's own numeric index token to `index`, keeping the rest of
    the name exactly as it is.

    The inverse of get_index_from_name, and deliberately sharing its pattern
    so the token read is the token written. Used to keep indices
    incrementing down a chain whose joint names do NOT follow the configured
    template: the artist's naming is theirs to keep, but the numbering still
    has to run in order.

    The LAST numeric token is the one rewritten, matching
    get_index_from_name's default. An 'ee' token is not an index and is
    never touched, so an end joint keeps its marker.

    Arguments:
        node (str): Node name or DAG path
        index (int): New index, formatted with rt_constants.DFORMAT
        underscore (bool): If True, index must be preceded by
            whitespace/underscore/dash

    Return:
        str: The leaf name with its index rewritten, unchanged when it has
        no numeric index token to rewrite

    Examples:
        replace_index_in_name('R_tail_01_jnt', 7) -> 'R_tail_07_jnt'
        replace_index_in_name('spine_ctrl', 7)    -> 'spine_ctrl'
        replace_index_in_name('R_tail_ee_jnt', 7) -> 'R_tail_ee_jnt'
    """
    if not node:
        return node
    leaf = node.split('|')[-1]
    pattern = _INDEX_PATTERN if underscore else _INDEX_PATTERN_ANY

    last = None
    for m in re.finditer(pattern, leaf):
        if m.group() != 'ee':
            last = m
    if last is None:
        return leaf
    return (leaf[:last.start()] + rt_constants.DFORMAT.format(int(index)) +
            leaf[last.end():])


def strip_group_suffix(name):
    """
    Strip a trailing group label (rt_constants.GRP) and its separator from a
    name, e.g. 'tail_root_grp' -> 'tail_root'. Names that do not end
    with the group label are returned unchanged (whitespace-stripped).

    Arguments:
        name (str): Name to strip

    Return:
        str: Name without the trailing group label
    """
    name = name.strip()
    grp = rt_constants.GRP
    if grp and name != grp and name.endswith(grp):
        stripped = name[:-len(grp)].rstrip('_- ')
        if stripped:
            return stripped
    return name


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


def name_matches_rigname(rigname, name):
    """
    Check if name is exactly the rigname, optionally followed by
    numeric index tokens: rigname 'tail' matches 'tail', 'tail_01',
    but not 'R_tail', 'tail_geo', or 'detail'.

    Arguments:
        rigname (str): Rigname to match
        name (str): Name to check (a DAG path is reduced to its leaf)

    Return:
        bool: True if name matches
    """
    sep = r'[_\-\s]+'
    idx = rf'(?:{sep}\d+)*'
    leaf = name.split('|')[-1]
    return re.match(rf'(?i)^{re.escape(rigname)}{idx}$', leaf) is not None


def name_contains_rigname_terms(rigname, name, terms=r'mesh|geo|geometry'):
    """
    Check if name is exactly rigname plus a terms token, in either
    order, with optional numeric index tokens in between or after
    (separated by underscore, dash, or space).

    Matching is anchored to the whole name, so rig parts that are
    substrings of one another cannot collide: rigname 'tail' matches
    'tail_geo', 'tail_01_geo', or 'geo_tail', but not 'R_tail_geo'
    (that mesh belongs to rig part 'R_tail').

    Arguments:
        rigname (str): Rigname to match
        name (str): Name to check (a DAG path is reduced to its leaf)
        terms (str): Regex pattern of terms to match

    Return:
        bool: True if name matches pattern
    """
    # Valid separators: underscore, dash, space
    sep = r'[_\-\s]+'
    # Optional numeric index tokens, e.g. tail_01_geo, tail_geo_01
    idx = rf'(?:{sep}\d+)*'
    leaf = name.split('|')[-1]
    rig = re.escape(rigname)
    pattern = (
        rf'(?i)^{rig}{idx}{sep}(?:{terms}){idx}$'
        rf'|^(?:{terms}){idx}{sep}{rig}{idx}$'
    )
    return re.match(pattern, leaf) is not None


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
