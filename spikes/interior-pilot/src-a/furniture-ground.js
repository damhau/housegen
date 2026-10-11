import fx from 'housekit/furnish';
import { ground } from './interior-layouts.js';
import { tools,tiles,bathDetails,entrance,smallLamp } from './furniture-tools.js';
import { dining,print,curtain } from './scandinavian-details.js';
import { linenBed } from './furniture-linen.js';
import { pilotBedroom,pilotBath } from './pilot-rooms.js';
export async function furnishGround(ctx){
 const t=tools(ctx,ground.y),r=ground.rooms,pi=Math.PI;
 // The permit's back-wall run, island and eight-place table.
 t.put(fx.kitchenRun({length:3.78,tall:[{at:.42,width:.84},{at:3.42,width:.72,oven:true}],hob:2.15,worktop:'oak'}),-3.185,-2.10);
 t.put(fx.kitchenRun({length:2.1,depth:.9,sink:1.08,upper:false,splash:null,worktop:'oak'}),-3.15,-.17);
 await dining(t,-3.17,2.58,2.12,.9,8);
 await t.model('coffee-machine-black',-3.85,-2.02,0,{width:.26,length:.35,height:.35},.9);
 await t.model('toaster',-3.42,-2.10,0,{width:.27},.9);await t.model('bottle-oil',-2.85,-2.28,0,{height:.22},.9);
 await t.model('planter-herbs',-3.92,-.19,0,{width:.3,height:.25},.91);await t.model('bowls-black',-2.49,-.15,0,{width:.24},.91);
 await t.model('vase-white',-3.17,2.55,0,{height:.24,width:.13},.755);
 await t.model('pendant-globe',-3.17,2.58,0,{width:.45,height:.8},2.6);t.glow(-3.17,2.58,1.88);t.light(-3.1,-1.25);t.light(-1.8,3.8);
 await t.model('pendant-globe',-2.75,-.17,0,{width:.26,height:.65},2.61);t.glow(-2.75,-.17,1.98);
 // Salon: sofa faces the pair of armchairs as drawn, circulation behind it.
 await t.model('sofa-grey-cushions',1.36,3.16,pi/2,{width:2.1,length:.8});
 t.put(fx.rug({width:2.48,depth:2.2,color:'#ded8cd'}),3.25,3.11);
 const table=await t.model('coffee-table-oval-white',3.12,3.0,0,{width:.78,length:.52});
 await t.model('book-open',3.07,3.0,.12,{width:.28,length:.20},table.userData.height+.005);
 await t.model('coffee-table-oval-black',3.44,3.47,0,{width:.60,length:.39,height:.35});
 await t.model('armchair-oak-leather',4.36,2.2,-pi/3,{width:.66,length:.72});
 await t.model('armchair-oak-leather',4.34,3.85,-pi*2/3,{width:.66,length:.72});
 await t.model('floor-lamp-black',.60,4.18,0,{height:1.72});t.glow(.6,4.18);
 await t.model('plant-leafy-white-pot',4.65,1.85,0,{width:.33,height:.62});
 await t.model('plant-large',-.55,4.21,0,{width:.42,height:1.15});
 t.light(2.7,2.7);
 await t.model('wall-art-gallery',5.045,3.13,-pi/2,{width:1.85,height:1.15,length:.035},.91);
 // Pilot: Chambre 1 and SDB restaged (pilot-rooms.js)
 await pilotBedroom(t,r[4]);
 await pilotBath(t,r[3]);
 // Narrow guest WC: small wall-hung hand basin leaves the door and middle clear.
 t.put(fx.wc({boxWidth:.49,tiles}),-.575,-.59);
 t.put(fx.basin({width:.38,depth:.26,vanity:false}),-.30,.46,-pi/2);
 t.put(fx.towelRail({width:.3,height:.65}),-.97,.39,pi/2,.65);t.tiled(r[2]);t.light(-.575,.75,2.40,1.2);
 // Entrance joinery stays in the two niches either side of the east door.
 t.put(fx.wardrobe({width:1.12,depth:.54,height:2.2}),4.48,1.22,pi);
 t.put(fx.bench({width:1.05,depth:.31}),4.48,-.74);t.put(fx.coatHooks({width:1.05,coats:['#c6bca7']}),4.48,-.91);
 await t.model('mirror-round',3.91,-.79,pi/2,{width:.5,height:.5,length:.035},1.13);t.light(3.5,.19);
 await entrance(t,r[6],4,.9,.85);t.light(-2,-3.8);t.sconce(-5.04,-3.6,pi/2);t.sconce(.18,.05,pi/2,2.0);t.sconce(-.18,-2.6,-pi/2,2.15);
 for(const x of [-4.03,-1.22,1.3,4.03])await t.model('curtain-grey',x,4.45,pi,{width:.30,height:2.36,length:.15});
 for(const z of [-.02,3.31])await curtain(t,-4.96,z,pi/2);
}
