import re, ifcopenshell
import bakedness as b

PANEL_OP_TO_OP = {
 "FIXEDCASEMENT":"FIXED","SIDEHUNGLEFTHAND":"CASEMENT","SIDEHUNGRIGHTHAND":"CASEMENT",
 "TOPHUNG":"AWNING","BOTTOMHUNG":"HOPPER","SLIDINGHORIZONTAL":"SLIDER",
 "SLIDINGVERTICAL":"HUNG","TILTANDTURNLEFTHAND":"TILTTURN","TILTANDTURNRIGHTHAND":"TILTTURN",
 "PIVOTHORIZONTAL":"PIVOTH","PIVOTVERTICAL":"PIVOTV","REMOVABLECASEMENT":"REMOVABLE",
}
NAME_OP = [("dreh.?kipp","TILTTURN"),("tilt.?turn","TILTTURN"),
 ("double.?hung","DOUBLEHUNG"),("single.?hung","SINGLEHUNG"),
 ("fixed","FIXED"),("fest","FIXED"),("casement","CASEMENT"),
 ("awning","AWNING"),("klapp","AWNING"),("hopper","HOPPER"),("kipp","HOPPER"),
 ("hung","HUNG"),("slid","SLIDER"),("schiebe","SLIDER"),
 ("tilt","TILTTURN"),("dreh","CASEMENT"),("pivot","PIVOT"),("schwing","PIVOTH"),
 ("skylight","SKYLIGHT"),("roof.?window","SKYLIGHT"),("dachflächenfenster","SKYLIGHT"),
 ("dachfenster","SKYLIGHT"),("oberlicht","SKYLIGHT")]
PART_MAP = {"SINGLE_PANEL":"SINGLE","DOUBLE_PANEL_VERTICAL":"DBL_V","DOUBLE_PANEL_HORIZONTAL":"DBL_H",
 "TRIPLE_PANEL_VERTICAL":"TRI_V","TRIPLE_PANEL_HORIZONTAL":"TRI_H"}

def classify(win, wt):
    name = ((win.Name or "") + " " + (wt.Name if wt else "")).lower()
    panel_ops = []
    for pd in (getattr(wt,"HasPropertySets",None) or []) if wt else []:
        if pd.is_a("IfcWindowPanelProperties"):
            panel_ops.append(getattr(pd,"OperationType",None))
    defined_ops=[o for o in panel_ops if o and o not in ("NOTDEFINED","USERDEFINED")]
    partition = getattr(wt,"PartitioningType",None) or getattr(wt,"OperationType",None) if wt else None
    predef = getattr(win,"PredefinedType",None) or (getattr(wt,"PredefinedType",None) if wt else None)

    op=None; conf=None; basis=None
    defined_up=[o.upper() for o in defined_ops]
    if predef and str(predef).upper()=="SKYLIGHT":     # skylight is a distinct style, check first
        op="SKYLIGHT"; conf,basis="high","predefined_type"
    elif defined_up:                                   # strongest: explicit panel operation enums
        n_sv=sum(o=="SLIDINGVERTICAL" for o in defined_up)
        n_fixed=sum(o=="FIXEDCASEMENT" for o in defined_up)
        mapped={PANEL_OP_TO_OP.get(o) for o in defined_up} - {None}
        if n_sv>=2:                       op="DOUBLEHUNG"   # two sliding sashes
        elif n_sv==1 and n_fixed>=1:      op="SINGLEHUNG"   # one sliding + one fixed
        elif n_sv==1:                     op="HUNG"         # one sliding, partner unknown
        elif mapped=={"CASEMENT"}:        op="CASEMENT"
        elif len(mapped)==1:              op=next(iter(mapped))
        else:                             op=sorted(mapped)[0] if mapped else "UNKNOWN"
        conf,basis="high","panel_operation_enum"
    else:                                              # fall back to family name
        for pat,o in NAME_OP:
            if re.search(pat,name): op=o; conf,basis="medium","family_name"; break
    if op is None:
        op="UNKNOWN"; conf,basis="low","none"

    # panel config
    if partition and str(partition).upper() in PART_MAP:
        pconf=PART_MAP[str(partition).upper()]; pbasis="partitioning_enum"
    elif len(defined_ops)>=2:
        pconf="DBL_V"; pbasis="panel_count"
    else:
        pconf="SINGLE"; pbasis="assumed"
    # operations that physically require multiple panels default to multi-panel when undeclared
    if op in ("SINGLEHUNG","DOUBLEHUNG","HUNG") and pconf=="SINGLE": pconf="DBL_H"
    if op=="SLIDER" and pconf=="SINGLE": pconf="DBL_V"

    shape="RECT"
    if re.search(r"rund|round|circ|oculus",name):
        shape="ROUND"
        if conf=="low": conf,basis="low","shape_name"
        if op=="UNKNOWN": op="FIXED"   # round windows are conventionally fixed (weak)

    # assemble BASE code (always a registry row); shape/handing carried as separate refinement fields
    if op=="UNKNOWN":
        code="UNCLASSIFIED"
    elif op=="SKYLIGHT":
        code="WIN-SKYLIGHT-SINGLE"
    else:
        code="-".join(["WIN",op,pconf])
    return dict(style_code=code, operation_guess=op, panel_config_guess=pconf,
                shape=shape, confidence=conf, basis=basis,
                evidence=f"name='{name.strip()[:30]}' part={partition} panel_ops={defined_ops or '-'}")

def golden_verdict(bakedness, conf, code):
    if bakedness in ("BAKED_WITH_METADATA","FULLY_BAKED","STUB_NO_GEOMETRY"):
        return "specimen_only"          # documents the input problem, not a target
    if code=="UNCLASSIFIED" or conf=="low":
        return "needs_style_review"     # clean enough, but style unconfirmed
    if conf=="high":
        return "candidate"
    return "candidate_review"           # medium-confidence classification
