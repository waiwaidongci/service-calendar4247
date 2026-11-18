import {loadAll,loadRecon} from '../api/business.js';
export const store={session:JSON.parse(localStorage.getItem('session')||'null'),data:{sources:[],periods:[],policies:[],runs:[],schedules:[],deliveries:[],audits:[],bills:[],billConflicts:[],billBatches:[],reconSessions:[],reconDetail:null},selected:null,selectedRecon:Number(sessionStorage.getItem('selectedRecon'))||null};
export async function reload(){store.data=await loadAll();if(store.selectedRecon){try{store.data.reconDetail=await loadRecon(store.selectedRecon)}catch(e){store.selectedRecon=null;sessionStorage.removeItem('selectedRecon');store.data.reconDetail=null}}else store.data.reconDetail=null}
export function setSession(value){store.session=value;if(value)localStorage.setItem('session',JSON.stringify(value));else localStorage.removeItem('session')}
export function selectRecon(id){store.selectedRecon=id;if(id)sessionStorage.setItem('selectedRecon',String(id));else sessionStorage.removeItem('selectedRecon')}
