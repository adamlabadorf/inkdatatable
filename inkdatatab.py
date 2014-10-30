#! /usr/bin/python
"""
inkex.py
A helper module for creating Inkscape extensions

Copyright (C) 2005,2007 Aaron Spike, aaron@ekips.org

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
"""
import sys, copy, optparse, random, re
import gettext
from itertools import cycle
from math import *
_ = gettext.gettext

#a dictionary of all of the xmlns prefixes in a standard inkscape doc
NSS = {
u'sodipodi' :u'http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd',
u'cc'     :u'http://creativecommons.org/ns#',
u'ccOLD'  :u'http://web.resource.org/cc/',
u'svg'    :u'http://www.w3.org/2000/svg',
u'dc'     :u'http://purl.org/dc/elements/1.1/',
u'rdf'    :u'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
u'inkscape' :u'http://www.inkscape.org/namespaces/inkscape',
u'xlink'  :u'http://www.w3.org/1999/xlink',
u'xml'    :u'http://www.w3.org/XML/1998/namespace'
}

#a dictionary of unit to user unit conversion factors
uuconv = {'in':90.0, 'pt':1.25, 'px':1, 'mm':3.5433070866, 'cm':35.433070866, 'm':3543.3070866,
      'km':3543307.0866, 'pc':15.0, 'yd':3240 , 'ft':1080}
def unittouu(string):
  '''Returns userunits given a string representation of units in another system'''
  unit = re.compile('(%s)$' % '|'.join(uuconv.keys()))
  param = re.compile(r'(([-+]?[0-9]+(\.[0-9]*)?|[-+]?\.[0-9]+)([eE][-+]?[0-9]+)?)')

  p = param.match(string)
  u = unit.search(string)  
  if p:
    retval = float(p.string[p.start():p.end()])
  else:
    retval = 0.0
  if u:
    try:
      return retval * uuconv[u.string[u.start():u.end()]]
    except KeyError:
      pass
  return retval

def uutounit(val, unit):
  return val/uuconv[unit]

try:
  from lxml import etree
except:
  sys.exit(_('The fantastic lxml wrapper for libxml2 is required by inkex.py and therefore this extension. Please download and install the latest version from http://cheeseshop.python.org/pypi/lxml/, or install it through your package manager by a command like: sudo apt-get install python-lxml'))

def debug(what):
  sys.stderr.write(str(what) + "\n")
  return what

def errormsg(msg):
  """Intended for end-user-visible error messages.
  
     (Currently just writes to stderr with an appended newline, but could do
     something better in future: e.g. could add markup to distinguish error
     messages from status messages or debugging output.)
    
     Note that this should always be combined with translation:

     import gettext
     _ = gettext.gettext
     ...
     inkex.errormsg(_("This extension requires two selected paths."))
  """
  sys.stderr.write((unicode(msg) + "\n").encode("UTF-8"))

def check_inkbool(option, opt, value):
  if str(value).capitalize() == 'True':
    return True
  elif str(value).capitalize() == 'False':
    return False
  else:
    raise optparse.OptionValueError("option %s: invalid inkbool value: %s" % (opt, value))

def addNS(tag, ns=None):
  val = tag
  if ns!=None and len(ns)>0 and NSS.has_key(ns) and len(tag)>0 and tag[0]!='{':
    val = "{%s}%s" % (NSS[ns], tag)
  return val

class InkOption(optparse.Option):
  TYPES = optparse.Option.TYPES + ("inkbool",)
  TYPE_CHECKER = copy.copy(optparse.Option.TYPE_CHECKER)
  TYPE_CHECKER["inkbool"] = check_inkbool

def parseStyle(style) :
    d = {}
    if style :
        for t in style.split(';') :
            k,v = t.split(':')
            d[k.strip()] = v.strip()
    return d

def formatStyle(style) :
    return ' '.join('%s: %s;'%i for i in style.items())

def check_flowbox(n) :
    try :
        if n.tag.endswith("rect") or \
          (n.tag.endswith("flowRoot") and \
           n[0].tag.endswith("flowRegion") and \
           n[0][0].tag.endswith("rect")) :
            return True
    except :
        pass
    return False

def check_rect(n) :
    return n.tag.endswith("rect")

class Effect:
  """A class for creating Inkscape SVG Effects"""

  def __init__(self, *args, **kwargs):
    self.document=None
    self.ctx=None
    self.selected={}
    self.doc_ids={}
    self.options=None
    self.args=None
    self.parser = optparse.OptionParser(usage="usage: %prog [options] SVGfile\n\nsys.argv: "+str(sys.argv),option_class=InkOption)
    self.parser.add_option("--id",
            action="append", type="string", dest="ids", default=[], 
            help="id attribute of object to manipulate")
    self.parser.add_option("--numrows",
            type="int", dest="numrows", default=5,
            help="Number of rows to create")
    self.parser.add_option("--row_rect",
            type="string", dest="row_rect", action="callback",
            callback=lambda _,opt,val,par : True if val=="true" else False,
            help="Create background rect only for every row, instead of for all cells")
    self.parser.add_option("--row_sep",
            type="int", dest="row_sep", default=0,
            help="Add this many pixels of space between rows")
    self.parser.add_option("--align",
            type="string", dest="align", default=None,
            help=("Align columns according to l, c, or r, e.g. 3 columns "
                  "aligned left, center, left would be --align=lcl"))

  def effect(self):

    if not self.options.ids :
        errormsg("No objects were selected, must select at least one object to "
                 "create a table\n")
        sys.exit(1)

    sel = self.selected.values()
    sel.sort(key=lambda n: float(n.get("x")))

    # table works in 3 creation modes:
    # only rects
    # rects with one text object
    # n rects with n text objects
    rect_sel = [s for s in sel if s.tag.endswith("rect")]
    text_sel = [s for s in sel if s.tag.endswith("text")]

    if len(text_sel) not in (0,1,len(rect_sel)) :
        errormsg("When specifying text objects, there must be exactly zero, "
                 "one, or a number equal to the number of rects.")
        sys.exit(1)

    if not all(map(check_rect,rect_sel)) :
        errormsg("All non-text selected objects must be rects")
        sys.exit(1)

    if len(rect_sel)+len(text_sel) != len(sel) :
        errormsg("Items other than rects and text are selected, remove them "
                 "from the selection")
        sys.exit(1)

    align_map = {'l':('start','start'),
                 'c':('center','middle'),
                 'r':('end','end')}
    align = self.options.align and [align_map[a] for a in self.options.align] or None
    if align and len(align) != len(rect_sel) :
        errormsg("When specifying alignment, must be exactly the same number "
                 "of [l|c|r] as rects")
        sys.exit(1)

    text_sel = cycle(text_sel or [None])
    align = cycle(align or [align_map['l']])

    # figure out top left corner
    lefts = [float(n.get("x")) for n in rect_sel]
    left = lefts[0]
    top = round(float(rect_sel[0].get("y")))

    # height needs to be an integer otherwise there are odd artefacts when
    # drawing
    height = floor(float(rect_sel[0].get("height")))

    widths = [float(n.get("width")) for n in rect_sel]
    styles = [n.get("style") for n in rect_sel]

    parent = self.current_layer
    table_g_attr = {'id': self.uniqueId("table_g"),
                    'transform': 'translate(%f,%f)'%(left,top) }
    table_g = etree.SubElement(parent,addNS("g","svg"), table_g_attr)

    cell_g_attr = {'id':self.uniqueId("cell_g"),
                   'transform': 'translate(0,0)'}
    cell_g = etree.SubElement(table_g,addNS("g","svg"), cell_g_attr)

    text_g_attr = {'id':self.uniqueId("text_g"),
                   'transform': 'translate(0,0)'}
    text_g = etree.SubElement(table_g,addNS("g","svg"), text_g_attr)

    for r_i in range(self.options.numrows) :
        for l,w,s in zip(lefts,widths,styles) :
            # cell
            x = float(l-left)
            h = round(height*r_i)
            y = h+r_i*self.options.row_sep
            attr = {'x': str(x),
                    'y': str(y),
                    'width': str(w),
                    'height': str(height),
                    'style': s
                    }
            etree.SubElement(cell_g,addNS("rect","svg"),attr)

            # text object
            t_y = y+height/2

            attr = {'y': str(t_y),
                    '{%s}space'%NSS['xml']: 'preserve',
                    'style': formatStyle({'baseline-shift': '-15%' })
                    }

            tspan_attr = {'style': 'baseline-shift: -10%;'}

            # try to figure out what the alignment was, if any
            t_align, t_anchor = 'start','start'
            curr_text_sel = text_sel.next()
            if curr_text_sel is not None :
                attr['style'] += curr_text_sel.get('style')
                style_d = parseStyle(attr['style'])

                t_align = style_d.get('text-align',t_align)
                t_anchor = style_d.get('text-anchor',t_anchor)

                if len(curr_text_sel) > 0 :
                    tspan_attr['style'] += curr_text_sel[0].get('style','')

            if t_align == "center" :
                t_x = x + w/2
            elif t_align == "end" :
                t_x = x + w-3
            else :
                t_x = x+3

            attr['x'] = str(t_x)

            text_node = etree.SubElement(text_g,addNS("text","svg"),attr)
            tspan_node = etree.SubElement(text_node,addNS("tspan","svg"),tspan_attr)
            tspan_node.text = "a"

    # make the bounding box

    # make the grid lines

  def getoptions(self,args=sys.argv[1:]):
    """Collect command line arguments"""
    self.options, self.args = self.parser.parse_args(args)

  def parse(self,file=None):
    """Parse document in specified file or on stdin"""
    try:
      try:
        stream = open(file,'r')
      except:
        stream = open(self.svg_file,'r')
    except:
      stream = sys.stdin
    self.document = etree.parse(stream)
    stream.close()

  def getposinlayer(self):
    #defaults
    self.current_layer = self.document.getroot()
    self.view_center = (0.0,0.0)

    layerattr = self.document.xpath('//sodipodi:namedview/@inkscape:current-layer', namespaces=NSS)
    if layerattr:
      layername = layerattr[0]
      layer = self.document.xpath('//svg:g[@id="%s"]' % layername, namespaces=NSS)
      if layer:
        self.current_layer = layer[0]

    xattr = self.document.xpath('//sodipodi:namedview/@inkscape:cx', namespaces=NSS)
    yattr = self.document.xpath('//sodipodi:namedview/@inkscape:cy', namespaces=NSS)
    doc_height = unittouu(self.document.getroot().get('height'))
    if xattr and yattr:
      x = xattr[0]
      y = yattr[0]
      if x and y:
        self.view_center = (float(x), doc_height - float(y)) # FIXME: y-coordinate flip, eliminate it when it's gone in Inkscape

  def getselected(self):
    """Collect selected nodes"""
    for i in self.options.ids:
      path = '//*[@id="%s"]' % i
      for node in self.document.xpath(path, namespaces=NSS):
        self.selected[i] = node

  def getElementById(self, id):
    path = '//*[@id="%s"]' % id
    el_list = self.document.xpath(path, namespaces=NSS)
    if el_list:
      return el_list[0]
    else:
      return None

  def getParentNode(self, node):
    for parent in self.document.getiterator():
      if node in parent.getchildren():
        return parent
        break


  def getdocids(self):
    docIdNodes = self.document.xpath('//@id', namespaces=NSS)
    for m in docIdNodes:
      self.doc_ids[m] = 1

  def getNamedView(self):
    return self.document.xpath('//sodipodi:namedview', namespaces=NSS)[0]

  def createGuide(self, posX, posY, angle):
    atts = {
      'position': str(posX)+','+str(posY),
      'orientation': str(sin(radians(angle)))+','+str(-cos(radians(angle)))
      }
    guide = etree.SubElement(
          self.getNamedView(),
          addNS('guide','sodipodi'), atts )
    return guide

  def output(self):
    """Serialize document into XML on stdout"""
    self.document.write(sys.stdout)

  def affect(self, args=sys.argv[1:], output=True):
    """Affect an SVG document with a callback effect"""
    self.svg_file = args[-1]
    self.getoptions(args)
    self.parse()
    self.getposinlayer()
    self.getselected()
    self.getdocids()
    self.effect()
    if output: self.output()

  def uniqueId(self, old_id, make_new_id = True):
    new_id = old_id
    if make_new_id:
      while new_id in self.doc_ids:
        new_id += random.choice('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
      self.doc_ids[new_id] = 1
    return new_id

  def xpathSingle(self, path):
    try:
      retval = self.document.xpath(path, namespaces=NSS)[0]
    except:
      errormsg(_("No matching node for expression: %s") % path)
      retval = None
    return retval
      

if __name__ == '__main__' :
    e = Effect().affect()

# vim: expandtab shiftwidth=4 tabstop=8 softtabstop=4 encoding=utf-8 textwidth=99
