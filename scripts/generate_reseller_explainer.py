from pathlib import Path
import math, wave
import numpy as np
import imageio.v2 as imageio
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'media'; OUT.mkdir(exist_ok=True)
W,H,FPS,DURATION=720,1280,15,81
BG='#07090d'; PANEL='#121720'; TEXT='#f7f8fb'; MUTED='#8d96a8'; GREEN='#a8ff3e'; CYAN='#67e8f9'
REG='C:/Windows/Fonts/segoeui.ttf'; BOLD='C:/Windows/Fonts/seguisb.ttf'; BLACK='C:/Windows/Fonts/seguibl.ttf'
def ft(n,black=False): return ImageFont.truetype(BLACK if black else BOLD if n>28 else REG,n)
def ease(x): x=max(0,min(1,x)); return 1-(1-x)**3
def base():
 im=Image.new('RGB',(W,H),BG); glow=Image.new('RGBA',(W,H),(0,0,0,0)); d=ImageDraw.Draw(glow); d.ellipse((350,-220,900,330),fill=(168,255,62,55)); glow=glow.filter(ImageFilter.GaussianBlur(90)); im.paste(glow,mask=glow); return im
def brand(d):
 d.rounded_rectangle((42,38,98,94),15,fill=GREEN); d.text((53,50),'LR',font=ft(22,1),fill='#071006'); d.text((116,50),'LUXE',font=ft(25,1),fill=TEXT); d.text((205,50),'RADAR',font=ft(25,1),fill=GREEN)
def text_center(d,text,y,f,fill=TEXT):
 box=d.textbbox((0,0),text,font=f); d.text(((W-box[2])/2,y),text,font=f,fill=fill)
def panel(d,box,r=25,outline='#252c38'): d.rounded_rectangle(box,r,fill=PANEL,outline=outline,width=2)
def badge(d,text,x,y,color=GREEN): d.rounded_rectangle((x,y,x+150,y+38),19,fill=color); d.text((x+15,y+8),text,font=ft(15,1),fill='#071006')
def title(d,kicker,lines):
 d.text((48,150),kicker,font=ft(17,1),fill=GREEN)
 for i,line in enumerate(lines): d.text((48,195+i*68),line,font=ft(52,1),fill=TEXT)
def search_scene(im,p):
 d=ImageDraw.Draw(im); title(d,'POUR LES REVENDEURS',['Une recherche.','Quatre sources.']); y=390; panel(d,(38,y,682,1105),30)
 fields=[('ARTICLE','Nike Trail'),('BUDGET MAXIMUM','50 €'),('SOURCES','Toutes les marketplaces')]
 for i,(lab,val) in enumerate(fields):
  yy=y+55+i*128; d.text((76,yy),lab,font=ft(15,1),fill=MUTED); d.rounded_rectangle((76,yy+30,644,yy+92),14,fill='#1a202b'); shown=val[:int(len(val)*ease(p*1.4-i*.12))]; d.text((96,yy+45),shown,font=ft(25),fill=TEXT)
 d.rounded_rectangle((76,yy+135,644,yy+205),16,fill=GREEN); text_center(d,'SCANNER MAINTENANT',yy+153,ft(22,1),'#071006')
def results_scene(im,p):
 d=ImageDraw.Draw(im); title(d,'CLASSEMENT INTELLIGENT',['Les meilleures','annonces d’abord.'])
 cards=[('eBay','Salomon Trail GTX','42 €','Score 94'),('Vinted','Nike Pegasus Trail','48 €','Score 91'),('Grailed','Nike ACG Trail','45 €','Score 87')]
 for i,(market,name,price,score) in enumerate(cards):
  local=ease(p*1.5-i*.15); x=int(38+(1-local)*W); y=390+i*220; panel(d,(x,y,x+644,y+185),24); badge(d,market,x+24,y+22); d.text((x+24,y+78),name,font=ft(27,1),fill=TEXT); d.text((x+490,y+30),price,font=ft(34,1),fill=GREEN); d.text((x+24,y+130),score+' · résultat réel',font=ft(18),fill=MUTED)
 text_center(d,'50 résultats, puis la suite automatiquement',1090,ft(19),MUTED)
def tools_scene(im,p):
 d=ImageDraw.Draw(im); title(d,'GARDE LE CONTRÔLE',['Observe. Compare.','Décide.']); tools=[('♡','Favoris','Garde les opportunités'),('!','Alertes','Relance tes recherches'),('⇄','Comparateur','Jusqu’à 4 annonces'),('↘','Suivi de prix','Repère les variations')]
 for i,(icon,name,sub) in enumerate(tools):
  x=38+(i%2)*327; y=420+(i//2)*250; panel(d,(x,y,x+307,y+220),24); d.text((x+24,y+25),icon,font=ft(40,1),fill=GREEN); d.text((x+24,y+93),name,font=ft(28,1),fill=TEXT); d.text((x+24,y+145),sub,font=ft(17),fill=MUTED)
def margin_scene(im,p):
 d=ImageDraw.Draw(im); title(d,'STUDIO REVENDEUR',['Calcule avant','d’acheter.']); panel(d,(38,400,682,1050),30); d.text((76,445),'CALCULATEUR DE MARGE',font=ft(18,1),fill=GREEN)
 vals=[('Prix d’achat','50 €'),('Prix de revente','100 €'),('Frais estimés','10 %')]
 for i,(lab,val) in enumerate(vals):
  yy=510+i*105; d.text((76,yy),lab,font=ft(18),fill=MUTED); d.text((535,yy),val,font=ft(25,1),fill=TEXT); d.line((76,yy+52,644,yy+52),fill='#293140',width=2)
 d.rounded_rectangle((76,850,644,980),22,fill='#1b2718',outline=GREEN,width=2); d.text((105,882),'40 € NET',font=ft(45,1),fill=GREEN); d.text((430,897),'80 %',font=ft(27,1),fill=TEXT)
def portfolio_scene(im,p):
 d=ImageDraw.Draw(im); title(d,'PORTFOLIO',['Ton stock.','Tes vrais chiffres.']); stats=[('CAPITAL INVESTI','740 €'),('VENTES','1 260 €'),('BÉNÉFICE','+ 382 €')]
 for i,(lab,val) in enumerate(stats):
  y=420+i*185; panel(d,(38,y,682,y+150),24); d.text((70,y+30),lab,font=ft(17,1),fill=MUTED); d.text((70,y+70),val,font=ft(42,1),fill=GREEN if i==2 else TEXT)
 d.text((48,1010),'Prix d’achat · frais · vente · statut',font=ft(21),fill=MUTED)
def tabs_scene(im,p):
 d=ImageDraw.Draw(im); title(d,'MOINS D’ONGLETS',['Plus de temps','pour vendre.']);
 for i,label in enumerate(['ANALYSER','NÉGOCIER','VENDRE']):
  x=65+i*210; d.ellipse((x,520,x+170,690),fill='#151c24',outline=GREEN,width=3); text_center_local=d.textbbox((0,0),label,font=ft(18,1))[2]; d.text((x+(170-text_center_local)/2,593),label,font=ft(18,1),fill=TEXT)
 d.line((150,790,570,790),fill=GREEN,width=8); d.ellipse((550,770,590,810),fill=GREEN); text_center(d,'Toutes tes décisions au même endroit.',870,ft(24),MUTED)
def cta_scene(im,p):
 d=ImageDraw.Draw(im); text_center(d,'TROUVE AVANT',360,ft(64,1),TEXT); text_center(d,'LES AUTRES.',435,ft(73,1),GREEN); text_center(d,'LUXE RADAR',580,ft(31,1),TEXT); d.rounded_rectangle((85,700,635,780),20,fill=GREEN); text_center(d,'COMMENCE GRATUITEMENT',722,ft(22,1),'#071006'); text_center(d,'Vinted · eBay · Grailed · 67behaviour',850,ft(17),MUTED); text_center(d,'Pensé pour chercher. Construit pour revendre.',915,ft(18),TEXT)
def render(t):
 im=base(); d=ImageDraw.Draw(im); brand(d)
 if t<11: title(d,'LA REVENTE COMMENCE ICI',['Trouver la bonne','pièce prend du temps.']); text_center(d,'Le bon prix. Le bon moment. Avant les autres.',560,ft(23),MUTED); d.arc((190,690,530,1030),195,520,fill=GREEN,width=14); a=t*2.8; d.line((360,860,360+135*math.cos(a),860+135*math.sin(a)),fill=GREEN,width=12); d.ellipse((343,843,377,877),fill=GREEN)
 elif t<24: search_scene(im,(t-11)/13)
 elif t<36: results_scene(im,(t-24)/12)
 elif t<47: tools_scene(im,(t-36)/11)
 elif t<59: margin_scene(im,(t-47)/12)
 elif t<69: portfolio_scene(im,(t-59)/10)
 elif t<75: tabs_scene(im,(t-69)/6)
 else: cta_scene(im,(t-75)/6)
 return im
def music():
 sr=44100; n=int(DURATION*sr); t=np.arange(n)/sr; audio=np.zeros(n); chords=[(110,138.59,164.81),(98,123.47,146.83),(82.41,110,138.59),(92.5,116.54,146.83)]
 for sec in range(DURATION):
  chord=chords[(sec//4)%4]; idx=slice(sec*sr,min((sec+1)*sr,n)); tt=t[idx]; pad=sum(np.sin(2*np.pi*f*tt)+.35*np.sin(2*np.pi*2*f*tt) for f in chord)/len(chord); pulse=.55+.45*np.sin(2*np.pi*1.5*tt)**2; audio[idx]=.055*pad*pulse
  for beat in (0,.5):
   start=int((sec+beat)*sr); end=min(start+int(.15*sr),n); x=np.arange(end-start)/sr; audio[start:end]+=.035*np.sin(2*np.pi*55*x)*np.exp(-25*x)
 fade=np.minimum(1,np.arange(n)/(2*sr))*np.minimum(1,(n-np.arange(n))/(2*sr)); audio*=fade; pcm=(np.clip(audio,-1,1)*32767).astype('<i2')
 path=OUT/'luxe_radar_reseller_musique.wav'; w=wave.open(str(path),'wb'); w.setparams((1,2,sr,n,'NONE','')); w.writeframes(pcm.tobytes()); w.close(); return path
def main():
 silent=OUT/'luxe_radar_reseller_sans_audio.mp4'; wr=imageio.get_writer(silent,fps=FPS,codec='libx264',quality=8,pixelformat='yuv420p',macro_block_size=16)
 for i in range(DURATION*FPS): wr.append_data(np.asarray(render(i/FPS)))
 wr.close(); render(77).save(OUT/'luxe_radar_reseller_miniature.png'); music(); print(silent)
if __name__=='__main__': main()
