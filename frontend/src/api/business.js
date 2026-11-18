import {request} from './client.js';
const ENDPOINTS={sources:'/sources',periods:'/periods',policies:'/policies',runs:'/runs',schedules:'/schedules',deliveries:'/deliveries',audits:'/audits',bills:'/bills',billConflicts:'/bills/conflicts',billBatches:'/bills/batches',reconSessions:'/recon/sessions'};
export const loadAll=async()=>Object.fromEntries(await Promise.all(Object.entries(ENDPOINTS).map(async([k,p])=>[k,await request(p)])));
export const loadRecon=id=>request('/recon/sessions/'+id);
export const saveSource=(data,id)=>request('/sources'+(id?'/'+id:''),id?'PUT':'POST',{data});
export const savePolicy=data=>request('/policies','POST',{data});
export const submitRun=data=>request('/runs','POST',data);
export const approveRun=(id,note)=>request('/runs/'+id+'/approve','POST',{note});
