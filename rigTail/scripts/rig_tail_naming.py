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
    find_mirror_pairs: Pair rig parts into (source, target) by side prefix
    mirror_partner: The part a rig part mirrors from, or None
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

# Rig part prefix that marks a mirrored side, e.g. 'L_fintail'.
SIDE_RE = re.compile(r'^([LlRr])_(.+)$')


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


def get_rigname(node, template, lenient=False):
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
        lenient (bool): Also accept a name that is the convention with its
            type labels left off, e.g. 'BN_C_fintail_1' for
            '{TYPE}_{rigname}_{NN}_{JNT}'. Off by default: callers that
            decide whether a node IS a rig part's node want the strict
            answer. See compile_template_to_regex.

    Note:
        The index token is optional in BOTH modes, so an unnumbered joint
        such as 'BN_L_leg_jnt' reads as rig part 'L_leg'. See
        compile_template_to_regex.

    Return:
        str or None: Extracted rigname, or None if not found
    """
    if '{rigname}' not in template:
        logger.warning('Invalid naming template. Ensure template includes {rigname}.')
        return None
    if not node:
        return None

    regex = compile_template_to_regex(template, lenient)
    m = regex.match(node.split('|')[-1])
    return m.group('rigname') if m else None


def compile_template_to_regex(template, lenient=False):
    """
    Compile naming template to regex pattern for matching.
    Known placeholders (TYPE, NN/nn, type labels such as JNT/GRP/CTRL)
    are resolved to their exact values; only {rigname} is captured.

    The index token is OPTIONAL, together with the separator in front of
    it: a one-joint chain is commonly authored unnumbered, and
    'BN_L_leg_jnt' is plainly rig part 'L_leg' - refusing to read it left
    the roster-from-selection button with nothing to offer but the joint's
    own name. A present index still wins, because {rigname} is non-greedy:
    'BN_L_tail3_00_jnt' is 'L_tail3' index 00, never 'L_tail3_00'. The
    TYPE prefix and (in strict mode) the type label still bracket the
    capture, so this does not widen the match to arbitrary node names.

    Arguments:
        template (str): Naming template with placeholders
        lenient (bool): Make the type-label placeholders (JNT, GRP, CTRL -
            anything resolving to a plain rt_constants string) optional,
            along with the separator in front of them.

            A half-named chain is the common case this exists for:
            'BN_C_fintail_1' is plainly rig part 'C_fintail' joint 1, but
            the strict pattern rejects it for want of a '_jnt', and a tool
            that cannot read it cannot conform it either. The TYPE stays
            REQUIRED, which is what keeps the leniency narrow:
            'tentacle_bone_01' still does not match, so genuinely
            hand-named chains are not swept into the convention. (The
            index is optional in both modes, so it is no longer the second
            guard it once was - callers that need a real index test for it
            with get_index_from_name, as _index_targets does.)

    Return:
        re.Pattern: Compiled regex pattern
    """
    # Break into tokens: {placeholder} or literal text
    tokens = re.findall(r'\{[^}]+\}|[^{}]+', template)
    # (kind, pattern) rather than bare patterns, so the lenient pass can
    # tell a type label from an index from a plain separator afterwards.
    parts = []

    for tok in tokens:
        if tok.startswith('{') and tok.endswith('}'):
            raw = tok[1:-1]
            leading, name, trailing = parse_placeholder(raw)

            # Insert leading literal
            if leading:
                parts.append(('literal', re.escape(leading)))

            # Capture rigname; resolve every other placeholder to its
            # exact value so the neighboring tokens anchor the capture:
            # NN/nn are indices (digits or 'ee'), TYPE is one of the
            # joint type labels, and labels like JNT/GRP/CTRL resolve
            # to their rt_constants constants. This keeps multi-token
            # rignames unambiguous without greedy captures:
            # 'BN_C_tail_00_jnt' -> 'C_tail', never 'C' or 'C_tail_00'.
            if name == 'rigname':
                parts.append(('rigname', r'(?P<rigname>.+?)'))
            elif name in ('NN', 'nn'):
                parts.append(('index', r'(?:\d+|ee)'))
            elif name == 'TYPE':
                types = [rt_constants.TYPE_BN, rt_constants.TYPE_IK,
                         rt_constants.TYPE_FK, rt_constants.TYPE_FX]
                parts.append(('type', '(?:' + '|'.join(
                    re.escape(t) for t in types) + ')'))
            else:
                const = getattr(rt_constants, name, None)
                if isinstance(const, str) and const:
                    parts.append(('label', re.escape(const)))
                else:
                    # Unknown placeholder (e.g. TAG): wildcard
                    parts.append(('wildcard', r'.+?'))

            # Insert trailing literal
            if trailing:
                parts.append(('literal', re.escape(trailing)))

        else:
            # Literal text outside placeholders
            parts.append(('literal', re.escape(tok)))

    # Index first, then labels: relaxing an index folds in the separator
    # BEFORE it, and relaxing a label folds in the separator before that
    # one, so each still finds a plain literal where it expects one.
    parts = _relax_indices(parts)
    if lenient:
        parts = _relax_labels(parts)

    pattern = ''.join(pattern for _, pattern in parts)
    return re.compile(pattern + r'\Z')


def _relax_indices(parts):
    """
    Make every index token optional, together with the separator in front
    of it, so an unnumbered name still parses.

    The separator has to come inside the optional group for the same reason
    it does in _relax_labels: with '{TYPE}_{rigname}_{NN}_{JNT}', making
    only the '{NN}' optional leaves 'BN_L_leg_jnt' one '_' short.

    Arguments:
        parts (list): (kind, pattern) pairs from compile_template_to_regex

    Return:
        list: the same pairs with 'index' entries relaxed
    """
    return _relax(parts, 'index')


def _relax_labels(parts):
    """
    Make every type label in a compiled template optional, together with the
    separator that precedes it.

    The separator has to come inside the optional group: with the template
    '{TYPE}_{rigname}_{NN}_{JNT}', making only the '{JNT}' optional leaves a
    dangling '_' that 'BN_C_fintail_1' still fails to supply.

    Arguments:
        parts (list): (kind, pattern) pairs from compile_template_to_regex

    Return:
        list: the same pairs with 'label' entries relaxed
    """
    return _relax(parts, 'label')


def _relax(parts, want):
    """
    Make every part of one kind optional, together with the separator that
    precedes it.

    Arguments:
        parts (list): (kind, pattern) pairs from compile_template_to_regex
        want (str): the kind to relax ('label' or 'index')

    Return:
        list: the same pairs with entries of that kind relaxed
    """
    out = []
    for kind, pattern in parts:
        if kind != want:
            out.append((kind, pattern))
            continue
        # Fold in a preceding separator-only literal ('_', '-', '.', ' ').
        # Anything else is real text the name still has to carry.
        sep = ''
        if out and out[-1][0] == 'literal':
            plain = re.sub(r'\\(.)', r'\1', out[-1][1])
            if re.fullmatch(r'[_\-. ]+', plain):
                sep = out.pop()[1]
        out.append((kind, f'(?:{sep}{pattern})?'))
    return out


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
    get_index_from_name's default. An existing 'ee' token is not an index
    and is never touched, so an end joint keeps its marker.

    Arguments:
        node (str): Node name or DAG path
        index (int or 'ee'): New index, formatted with rt_constants.DFORMAT.
            Pass 'ee' to write the end-joint marker in the index's place,
            which is how an end joint is named for a chain that does not
            follow the template - 'squiggle_04_bone' -> 'squiggle_ee_bone'.
        underscore (bool): If True, index must be preceded by
            whitespace/underscore/dash

    Return:
        str: The leaf name with its index rewritten, unchanged when it has
        no numeric index token to rewrite

    Examples:
        replace_index_in_name('R_tail_01_jnt', 7)    -> 'R_tail_07_jnt'
        replace_index_in_name('spine_ctrl', 7)       -> 'spine_ctrl'
        replace_index_in_name('R_tail_ee_jnt', 7)    -> 'R_tail_ee_jnt'
        replace_index_in_name('R_tail_01_jnt', 'ee') -> 'R_tail_ee_jnt'
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
    token = 'ee' if index == 'ee' else rt_constants.DFORMAT.format(int(index))
    return leaf[:last.start()] + token + leaf[last.end():]


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


def find_mirror_pairs(rigparts):
    """
    Pair rig parts into (source, target) by their side prefix.

    A pair exists when both an 'L_<base>' and an 'R_<base>' rig part are
    present (prefix match is case-insensitive; the base must be identical).
    The source side is rt_constants.MIRROR_SOURCE_SIDE (default 'R'); the
    other side is the target that gets overwritten. Center and unpaired
    parts are ignored - a lone source side names a target that RIGPARTS
    does not list, which is rig_tail_setup._implied_mirror_pairs' business
    and only Mirror Joints can act on, so it is not a pair until that has
    built the chain.

    Lives here rather than in rig_tail_setup because the build needs it
    too (rig_tail_mirror), and rig_tail_setup.py is an optional install a
    Builder-only setup leaves off disk. rig_tail_setup re-exports the name.

    Arguments:
        rigparts (list): RIGPARTS names.

    Return:
        tuple: (pairs, paired_names).
            pairs (list): [(source_rigname, target_rigname), ...].
            paired_names (set): every rigname that belongs to a pair.
    """
    source_side = str(getattr(rt_constants, 'MIRROR_SOURCE_SIDE', 'R')).upper()
    groups = {}
    for rp in rigparts:
        m = SIDE_RE.match(rp)
        if not m:
            continue
        groups.setdefault(m.group(2), {})[m.group(1).upper()] = rp

    pairs = []
    paired = set()
    for base, sides in groups.items():
        if 'L' in sides and 'R' in sides:
            target_side = 'L' if source_side == 'R' else 'R'
            pairs.append((sides[source_side], sides[target_side]))
            paired.add(sides['L'])
            paired.add(sides['R'])
    return pairs, paired


def mirror_partner(rigname, rigparts=None):
    """
    The rig part `rigname` mirrors FROM, or None when it mirrors nothing.

    Only the TARGET side of a pair gets an answer, so a caller acting on
    the result moves exactly one side of the pair and leaves the authored
    source alone. A center part, an unpaired part and the source side all
    return None.

    Arguments:
        rigname (str): Name of rig component
        rigparts (list): Roster to pair over; None reads rt_constants.RIGPARTS

    Return:
        str or None: the source-side partner, or None
    """
    parts = rt_constants.RIGPARTS if rigparts is None else rigparts
    for source, target in find_mirror_pairs(parts)[0]:
        if target == rigname:
            return source
    return None


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
