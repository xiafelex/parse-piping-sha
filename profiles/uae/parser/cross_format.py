"""Independent PCF/POD and SHA/POD checks. Keep ambiguous matches as lists."""
import math
import re
from collections import defaultdict


def read_pcf(path):
    blocks, current = [], None
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if line == 'MATERIALS': break
        if re.fullmatch(r'[A-Z][A-Z-]*', line):
            current = {'type': line, 'points': [], 'attributes': {}}
            blocks.append(current)
        elif current:
            words=line.strip().split(None,1)
            if len(words)!=2: continue
            key,value=words
            current['attributes'][key]=value
            if key in ('END-POINT','CENTRE-POINT','BRANCH1-POINT','CO-ORDS'):
                current['points'].append({'kind':key, 'xyz':list(map(float,value.split()[:3]))})
    return blocks


def line_key(xy):
    return tuple(sorted((tuple(round(x) for x in xy[:2]),tuple(round(x) for x in xy[2:]))))


def compare(model, pcf=None, sha=None):
    result={}
    if pcf is not None:
        blocks=read_pcf(pcf)
        by_uci=defaultdict(list)
        by_id={b['attributes'].get('COMPONENT-IDENTIFIER'):b for b in blocks if b['attributes'].get('COMPONENT-IDENTIFIER')}
        for b in blocks:
            if b['attributes'].get('UCI'):by_uci[b['attributes']['UCI']].append(b)
        pod_ucis={c['uci'] for c in model['components']}
        checks=[];attr_checks=[]
        for c in model['components']:
            candidates=by_uci.get(c['uci'],[])
            coordinates=[p['xyz'] for b in candidates for p in b['points']]
            for port in c['ports']:
                # Compare to all same-UCI PCF coordinates: split PIPE UCI repeats.
                distance=min((math.dist(port['xyz'],p) for p in coordinates),default=None)
                check={'pod_offset':port['offset'],'uci':c['uci'],'port':port['name'],'nearest_pcf_distance_mm':distance,'within_0_011mm':distance is not None and distance<=0.011}
                if not check['within_0_011mm']:
                    master_points=[p for b in candidates for master in [by_id.get(b['attributes'].get('MASTER-COMPONENT-IDENTIFIER'))] if master for p in master['points'] if p['kind']=='CENTRE-POINT']
                    master_distance=min((math.dist(port['xyz'],p['xyz']) for p in master_points),default=None)
                    check['master_centre_distance_mm']=master_distance
                    if master_distance is not None and master_distance<=0.011:
                        check['explanation']='POD centre matches explicit PCF master-component centre; not the bolt endpoint coordinate'
                    elif c['type_name']=='Eccentric-Reducer' and port['name'].startswith('Centre_'):
                        check['explanation']='POD eccentric reducer auxiliary centre; PCF supplies only end points, not an identical point role'
                checks.append(check)
            for prop in c['properties']:
                name=prop['name']
                expected={b['attributes'][name] for b in candidates if name in b['attributes']}
                if name.startswith('COMPONENT-ATTRIBUTE') and expected:
                    attr_checks.append({'offset':prop['offset'],'name':name,'name_evidence':prop['name_evidence'],'matches_pcf':prop['value'] in expected,'expected':sorted(expected),'pod_value':prop['value']})
        result['pcf']={'unique_uci_count':len(by_uci),'pod_unique_uci_count':len(pod_ucis),'missing_in_pod':sorted(set(by_uci)-pod_ucis),'extra_in_pod':sorted(pod_ucis-set(by_uci)),
                       'ports_checked':len(checks),'ports_within_tolerance':sum(x['within_0_011mm'] for x in checks),'port_checks':checks,
                       'attribute_checks':attr_checks,'attribute_matches':sum(x['matches_pcf'] for x in attr_checks)}
    if sha is not None:
        by_line=defaultdict(list)
        for name,info in sha['sheets'].items():
            for line in info['lines']:
                by_line[line_key([v*1e6 for v in line['xy_metres']])].append({'sheet':name,**line})
        checks=[]
        for group in model['graphics']:
            for p in group['primitives']:
                if p['kind']!='line':continue
                matches=by_line.get(line_key(p['xy']),[])
                checks.append({'pod_offset':p['offset'],'pod_owner_uci':group.get('uci'), 'sha_matches':matches})
        result['sha']={'pod_lines':len(checks),'exact_geometry_matches':sum(bool(c['sha_matches']) for c in checks),'drawing_scale_sha_to_pod':1000000,'line_correspondence':checks,
                       'pod_ucis_present_as_sha_strings':len({c['uci'] for c in model['components']} & set(sha['uci_strings'])),
                       'association_note':'POD containment gives UCI owner; SHA matches are exact segment geometry candidates, duplicate geometry does not imply unique SHA ownership'}
        arc_checks=[]
        for group in model['graphics']:
            for p in group['primitives']:
                if p['kind']!='arc':continue
                matches=[]
                for name,info in sha['sheets'].items():
                    for a in info['arcs']:
                        cx=a['center_x']/16800*1e6;cy=a['center_y']/16800*1e6;radius=a['radius']/16800*1e6
                        start=math.degrees(a['start_angle'])%360
                        end=math.degrees(a['end_angle'])%360
                        signed=1 if p['sweep_millidegrees']>=0 else 0
                        angle_delta=lambda x,y:abs((x-y+180)%360-180)
                        if abs(cx-p['center'][0])<.01 and abs(cy-p['center'][1])<.01 and abs(radius-p['radii'][0])<.01 and p['radii'][0]==p['radii'][1] and angle_delta(start,p['start_millidegrees']/1000)<1e-7 and angle_delta(end,(p['start_millidegrees']+p['sweep_millidegrees'])/1000)<1e-7 and a['direction']==signed:
                            matches.append({'sheet':name,**a})
                arc_checks.append({'pod_offset':p['offset'],'matches':matches})
        result['sha']['arc_checks']=arc_checks
        result['sha']['exact_arc_matches']=sum(bool(a['matches']) for a in arc_checks)
    return result
